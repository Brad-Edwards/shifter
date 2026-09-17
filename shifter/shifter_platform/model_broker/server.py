"""Participant listener: authenticated, budgeted, fenced provider streaming."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from contextlib import suppress
from uuid import uuid4

from pydantic import ValidationError

from shared.model_access import ContractError
from shared.model_access.credentials import ModelAccessAuthorization
from shared.model_access.http import body, headers, json_response
from shared.model_access.messages import MAX_MESSAGE_BYTES, MAX_RESPONSE_BYTES, parse_messages, strict_json

from .errors import NoBillableEffect


class BrokerApplication:
    """No generic proxy, credential issuance or public control surface."""

    def __init__(
        self,
        *,
        control,
        providers,
        fingerprint_key: bytes,
        key_version: str,
        previous_keys: dict[str, bytes] | None = None,
    ):
        if len(fingerprint_key) < 32:
            raise ValueError("broker fingerprint key needs at least 256 bits")
        self.control = control
        self.providers = providers
        self.fingerprint_key = fingerprint_key
        self.key_version = key_version
        self.previous_keys = dict(previous_keys or {})
        if (
            key_version in self.previous_keys
            or len(self.previous_keys) > 8
            or any(len(key) < 32 for key in self.previous_keys.values())
        ):
            raise ValueError("invalid retained broker fingerprint keys")
        self.draining = False

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        try:
            path = scope.get("path", "")
            if scope.get("query_string"):
                raise ContractError("broker.invalid_route")
            if scope["method"] == "GET" and path in {"/health/live", "/health/ready"}:
                healthy = path.endswith("live") or not self.draining
                await json_response(send, 200 if healthy else 503, {"ready": healthy})
                return
            if self.draining:
                raise ContractError("broker.draining")
            allowed = {"/v1/messages", "/v1/messages/count_tokens", "/v1/access/exchange", "/v1/access/refresh"}
            if scope["method"] != "POST" or path not in allowed:
                raise ContractError("broker.invalid_route")
            request_headers = headers(scope)
            if request_headers.get("content-type", "").split(";")[0] != "application/json":
                raise ContractError("broker.invalid_content_type")
            peer = (scope.get("client") or ("",))[0]
            raw = await body(receive, limit=MAX_MESSAGE_BYTES)
            if path.startswith("/v1/access/"):
                await self._exchange(path, peer, raw, send)
                return
            if request_headers.get("anthropic-version") != "2023-06-01" or request_headers.get("anthropic-beta"):
                raise ContractError("messages.unsupported_version")
            token = self._token(request_headers)
            authority = ModelAccessAuthorization.model_validate(
                await self.control.call(
                    "authenticate",
                    {
                        "token": token,
                        "transport_peer": peer,
                    },
                )
            )
            count_only = path.endswith("count_tokens")
            message = parse_messages(raw, count_only=count_only, limits=authority.limits)
            if message.model not in authority.aliases:
                raise ContractError("messages.alias_unavailable")
            shard = authority.aliases[message.model]
            required = "token-count" if count_only else "messages"
            if required not in shard.capabilities:
                raise ContractError("messages.capability_unavailable")
            provider = self.providers.build(shard, authority.limits)
            await self._invoke(provider, message, authority, token, peer, request_headers, count_only, receive, send)
        except ContractError as exc:
            status = 401 if exc.code == "credential.unavailable" else 409
            await json_response(
                send, status, {"type": "error", "error": {"type": "invalid_request_error", "message": exc.code}}
            )
        except (ValueError, ValidationError, TimeoutError):
            await json_response(
                send,
                400,
                {"type": "error", "error": {"type": "invalid_request_error", "message": "broker.invalid_request"}},
            )
        except Exception:
            await json_response(
                send, 503, {"type": "error", "error": {"type": "api_error", "message": "broker.unavailable"}}
            )

    async def _lifespan(self, receive, send):
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                self.draining = True
                await self.control.close()
                await self.providers.close()
                await send({"type": "lifespan.shutdown.complete"})
                return

    @staticmethod
    def _token(request_headers):
        api_key = request_headers.get("x-api-key", "")
        authorization = request_headers.get("authorization", "")
        if api_key and authorization:
            raise ContractError("credential.unavailable")
        token = api_key or (authorization[7:] if authorization.startswith("Bearer ") else "")
        if len(token) != 80:
            raise ContractError("credential.unavailable")
        return token

    async def _exchange(self, path, peer, raw, send):
        payload = strict_json(raw, limit=4096)
        if set(payload) != {"token"} or not isinstance(payload["token"], str) or len(payload["token"]) != 80:
            raise ContractError("credential.unavailable")
        result = await self.control.call(path.rsplit("/", 1)[1], {"token": payload["token"], "transport_peer": peer})
        await json_response(send, 200, result)

    def _retry_fields(self, request_headers, message, *, count_only):
        key = request_headers.get("idempotency-key")
        if key is None:
            return {}
        if not 1 <= len(key) <= 128 or not key.isascii():
            raise ContractError("messages.invalid_retry_key")
        intent = json.dumps(
            {"count_only": count_only, "body": message.model_dump(mode="json")}, sort_keys=True, separators=(",", ":")
        ).encode()

        def digest(data):
            return hmac.new(self.fingerprint_key, data, hashlib.sha256).hexdigest()

        return {
            "caller_key_hmac": digest(b"key\0" + key.encode()),
            "intent_fingerprint_hmac": digest(b"intent\0" + intent),
            "key_version": self.key_version,
            "prior_caller_key_hmacs": [
                hmac.new(secret, b"key\0" + key.encode(), hashlib.sha256).hexdigest()
                for secret in self.previous_keys.values()
            ],
            "prior_intent_fingerprint_hmacs": [
                hmac.new(secret, b"intent\0" + intent, hashlib.sha256).hexdigest()
                for secret in self.previous_keys.values()
            ],
            "retained_key_versions": list(self.previous_keys),
        }

    async def _invoke(self, provider, message, authority, token, peer, request_headers, count_only, receive, send):
        request_uuid = str(uuid4())
        identity = {"token": token, "transport_peer": peer, "request_uuid": request_uuid}
        bounded = provider.billing_bound(message, count_only=count_only)
        reserved = await self.control.call(
            "reserve",
            {
                **identity,
                "logical_alias": message.model,
                "billing_bound": bounded.model_dump(mode="json"),
                **self._retry_fields(request_headers, message, count_only=count_only),
            },
        )
        if reserved["request_uuid"] != request_uuid:
            await json_response(send, 409, {"type": "completed", "request_id": reserved["request_uuid"]})
            return
        dispatched = False
        state = {"started": False}
        settled = False
        work = heartbeat = disconnect = None
        try:
            lease = await self.control.call("advance", {**identity, "action": "dispatch"})
            dispatched = True
            await self.control.call(
                "advance", {**identity, "action": "check", "dispatch_token": lease["dispatch_token"]}
            )

            async with asyncio.timeout(min(120, authority.limits.max_request_seconds)):
                work = asyncio.create_task(
                    self._transfer(provider, message, count_only, send, state, request_uuid, identity)
                )
                heartbeat = asyncio.create_task(self._renew(identity))
                disconnect = asyncio.create_task(self._disconnected(receive))
                done, _ = await asyncio.wait({work, heartbeat, disconnect}, return_when=asyncio.FIRST_COMPLETED)
                for task in (heartbeat, disconnect):
                    if task in done:
                        task.result()
                usage = work.result()
                await self.control.call(
                    "finish", {"request_uuid": request_uuid, "action": "settle", "usage": usage.model_dump(mode="json")}
                )
                settled = True
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        except NoBillableEffect as exc:
            await self.control.call(
                "finish", {"request_uuid": request_uuid, "action": "settle", "usage": exc.usage.model_dump(mode="json")}
            )
            settled = True
            raise
        except Exception:
            if state["started"]:
                # A stream that fails cannot become a second HTTP response or
                # silently look complete. No upstream exception/body is reflected.
                await send(
                    {
                        "type": "http.response.body",
                        "body": (
                            b'\n\nevent: error\ndata: {"type":"error",'
                            b'"error":{"type":"api_error","message":"broker.interrupted"}}\n\n'
                        ),
                        "more_body": False,
                    }
                )
            else:
                raise
        finally:
            for task in (work, heartbeat, disconnect):
                if task is not None:
                    task.cancel()
            for task in (work, heartbeat, disconnect):
                if task is not None:
                    with suppress(asyncio.CancelledError, Exception):
                        await task
            if not settled:
                with suppress(Exception):
                    await self.control.call(
                        "finish", {"request_uuid": request_uuid, "action": "unknown" if dispatched else "release"}
                    )

    async def _transfer(self, provider, message, count_only, send, state, request_uuid, identity):
        total = 0

        async def before_transport():
            result = await self.control.call("advance", {**identity, "action": "continue"})
            return result["deadline"]

        async with provider.invoke(message, count_only=count_only, before_transport=before_transport) as response:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", response.content_type.encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-request-id", request_uuid.encode()),
                    ],
                }
            )
            state["started"] = True
            async for chunk in response.chunks:
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise ContractError("provider.response_limit")
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            return response.usage

    async def _renew(self, identity):
        while True:
            await asyncio.sleep(2)
            await self.control.call("advance", {**identity, "action": "continue"})

    @staticmethod
    async def _disconnected(receive):
        while True:
            if (await receive())["type"] == "http.disconnect":
                raise ContractError("http.disconnected")
