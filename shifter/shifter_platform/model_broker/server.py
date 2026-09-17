"""Participant listener: authenticated, budgeted, fenced provider streaming."""

from __future__ import annotations

import hashlib
import hmac
import json

from shared.model_access import ContractError
from shared.model_access.credentials import ModelAccessAuthorization
from shared.model_access.http import ASGIScope, Receive, Send, body, headers, json_response
from shared.model_access.messages import MAX_MESSAGE_BYTES, CountTokensRequest, JsonObject, parse_messages, strict_json

from .control import ControlClient
from .execution import BrokerInvocation, BrokerRequest
from .providers import ProviderRegistry


class BrokerApplication:
    """No generic proxy, credential issuance or public control surface."""

    def __init__(
        self,
        *,
        control: ControlClient,
        providers: ProviderRegistry,
        fingerprint_key: bytes,
        key_version: str,
        previous_keys: dict[str, bytes] | None = None,
    ) -> None:
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

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
        elif scope["type"] == "http":
            await self._http(scope, receive, send)

    async def _http(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        """Map bounded routing failures to fixed participant-safe envelopes."""
        try:
            path = scope.get("path", "")
            if scope.get("query_string"):
                raise ContractError("broker.invalid_route")
            if scope["method"] == "GET" and path in {"/health/live", "/health/ready"}:
                healthy = path.endswith("live") or not self.draining
                await json_response(send, 200 if healthy else 503, {"ready": healthy})
            else:
                await self._post(scope, receive, send)
        except ContractError as exc:
            status = 401 if exc.code == "credential.unavailable" else 409
            await _error(send, status, "invalid_request_error", exc.code)
        except (ValueError, TimeoutError):
            await _error(send, 400, "invalid_request_error", "broker.invalid_request")
        except Exception:
            await _error(send, 503, "api_error", "broker.unavailable")

    async def _post(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        """Admit only JSON access and Messages operations on fixed routes."""
        if self.draining:
            raise ContractError("broker.draining")
        path = scope["path"]
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
        else:
            request = await self._authorize_message(path, peer, raw, request_headers)
            provider = self.providers.build(request.authority.aliases[request.message.model], request.authority.limits)
            await BrokerInvocation(self.control, provider, request, receive, send).run()

    async def _authorize_message(
        self, path: str, peer: str, raw: bytes, request_headers: dict[str, str]
    ) -> BrokerRequest:
        """Authenticate and enforce the logical model's declared capabilities."""
        if request_headers.get("anthropic-version") != "2023-06-01" or request_headers.get("anthropic-beta"):
            raise ContractError("messages.unsupported_version")
        token = self._token(request_headers)
        authority = ModelAccessAuthorization.model_validate(
            await self.control.call("authenticate", {"token": token, "transport_peer": peer})
        )
        count_only = path.endswith("count_tokens")
        message = parse_messages(raw, count_only=count_only, limits=authority.limits)
        if message.model not in authority.aliases:
            raise ContractError("messages.alias_unavailable")
        required = "token-count" if count_only else "messages"
        if required not in authority.aliases[message.model].capabilities:
            raise ContractError("messages.capability_unavailable")
        return BrokerRequest(
            message,
            authority,
            token,
            peer,
            count_only,
            self._retry_fields(request_headers, message, count_only=count_only),
        )

    async def _lifespan(self, receive: Receive, send: Send) -> None:
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
    def _token(request_headers: dict[str, str]) -> str:
        api_key = request_headers.get("x-api-key", "")
        authorization = request_headers.get("authorization", "")
        if api_key and authorization:
            raise ContractError("credential.unavailable")
        token = api_key or (authorization[7:] if authorization.startswith("Bearer ") else "")
        if len(token) != 80:
            raise ContractError("credential.unavailable")
        return token

    async def _exchange(self, path: str, peer: str, raw: bytes, send: Send) -> None:
        payload = strict_json(raw, limit=4096)
        if set(payload) != {"token"} or not isinstance(payload["token"], str) or len(payload["token"]) != 80:
            raise ContractError("credential.unavailable")
        result = await self.control.call(path.rsplit("/", 1)[1], {"token": payload["token"], "transport_peer": peer})
        await json_response(send, 200, result)

    def _retry_fields(
        self, request_headers: dict[str, str], message: CountTokensRequest, *, count_only: bool
    ) -> JsonObject:
        key = request_headers.get("idempotency-key")
        if key is None:
            return {}
        if not 1 <= len(key) <= 128 or not key.isascii():
            raise ContractError("messages.invalid_retry_key")
        intent = json.dumps(
            {"count_only": count_only, "body": message.model_dump(mode="json")}, sort_keys=True, separators=(",", ":")
        ).encode()

        def digest(data: bytes) -> str:
            """Fingerprint only bounded request metadata under the active key."""
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


async def _error(send: Send, status: int, category: str, message: str) -> None:
    """Emit the fixed Messages error envelope without exception diagnostics."""
    await json_response(send, status, {"type": "error", "error": {"type": category, "message": message}})
