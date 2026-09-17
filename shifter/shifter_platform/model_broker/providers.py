"""Vertex and Bedrock invocation through fixed, deployment-approved origins."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from shared.model_access import BillingBound, ContractError
from shared.model_access.messages import MAX_RESPONSE_BYTES, strict_json
from shared.model_access.provider import (
    BillingAmount,
    CancellationResult,
    ProviderAdapterRegistry,
    ProviderCapabilities,
    ProviderUsage,
    VerifiedUsage,
)

from .errors import NoBillableEffect
from .provider_usage import StreamUsage, bedrock_events, encode_sse, usage_from_message, vertex_events


@dataclass
class ProviderResponse:
    """Streaming content plus usage populated only after a complete upstream reply."""

    content_type: str
    chunks: object
    usage: ProviderUsage | None = None


class ProviderRegistry:
    """Exact shard inventory; no provider/model fallback on failure."""

    def __init__(self, *, inventory, credentials, client=None):
        self.targets = {target.shard_id: target for target in inventory.targets}
        self.credentials = credentials
        self.client = client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(5, connect=1, pool=0.25, write=1),
            limits=httpx.Limits(max_connections=128),
        )

    def build(self, shard, limits):
        target = self.targets.get(shard.shard_id)
        if target is None:
            raise ContractError("provider.target_unavailable")
        target.bind(shard)
        if not {"input_tokens", "output_tokens"}.issubset(shard.billing_components):
            raise ContractError("provider.billing_unsupported")
        registry = ProviderAdapterRegistry(
            {
                target.provider: lambda bound_shard: MessagesProvider(
                    target=target.bind(bound_shard),
                    limits=limits,
                    credentials=self.credentials,
                    client=self.client,
                )
            }
        )
        return registry.build(shard)

    async def close(self):
        await self.client.aclose()


class MessagesProvider:
    """Text/local-tool protocol with conservative full-context spend reservation."""

    def __init__(self, *, target, limits, credentials, client):
        self.target, self.limits, self.credentials, self.client = target, limits, credentials, client

    def capabilities(self):
        return ProviderCapabilities(
            adapter_id=self.target.provider,
            protocols=("anthropic-messages/2023-06-01",),
            models=(self.target.model,),
            capabilities=("messages", "token-count"),
            billing_components=("input_tokens", "output_tokens", "request"),
            trustworthy_usage_components=("input_tokens", "output_tokens", "request"),
            streaming=True,
            token_counting=True,
            cancellation=False,
            completion_horizon_seconds=900,
        )

    def normalize_usage(self, provider_result):
        return usage_from_message(provider_result)

    def cancel(self, provider_request_id):
        # Closing a stream ends our transport; it does not prove a provider job
        # stopped. Preserve the conservative liability through reconciliation.
        from shared.model_access.provider import CancellationDisposition

        return CancellationResult(disposition=CancellationDisposition.UNSUPPORTED)

    def billing_bound(self, message=None, *, count_only=False, model=None, features=(), request_bytes=0):
        if model is not None and (model != self.target.model or set(features) - {"messages", "token-count"}):
            raise ContractError("provider.capability_mismatch")
        if request_bytes > self.limits.max_request_bytes:
            raise ContractError("messages.too_large")
        count_only = count_only or features == ("token-count",)

        # Count endpoints are free but still consume rate/concurrency. The
        # catalog carries a zero-price `request` component for this operation.
        amounts = (
            [("request", 1)]
            if count_only
            else [
                ("input_tokens", self.target.context_window_tokens),
                ("output_tokens", message.max_tokens if message is not None else self.limits.max_output_tokens),
            ]
        )
        return BillingBound(
            amounts=tuple(
                BillingAmount(component=key, units=value, maximum_charge_micro_units=0) for key, value in amounts
            )
        )

    def _request(self, message, *, count_only):
        target = self.target
        payload = message.model_dump(mode="json", exclude_none=True)
        payload.pop("model", None)
        if count_only:
            for field in ("max_tokens", "stream", "temperature", "top_p", "top_k", "stop_sequences"):
                payload.pop(field, None)
        stream = bool(payload.get("stream"))
        if target.provider == "vertex-v1":
            region = target.count_region if count_only else target.region
            origin = f"https://{region}-aiplatform.googleapis.com"
            model = "count-tokens" if count_only else target.model.rsplit("/", 1)[1]
            method = "streamRawPredict" if stream else "rawPredict"
            url = (
                f"{origin}/v1/projects/{target.project}/locations/{region}/publishers/anthropic/models/{model}:{method}"
            )
            if count_only:
                payload["model"] = target.model.rsplit("/", 1)[1]
            else:
                payload["anthropic_version"] = "vertex-2023-10-16"
        else:
            origin = f"https://bedrock-runtime.{target.region}.amazonaws.com"
            payload.pop("stream", None)
            payload["anthropic_version"] = "bedrock-2023-05-31"
            method = "count-tokens" if count_only else "invoke-with-response-stream" if stream else "invoke"
            url = f"{origin}/model/{quote(target.model, safe='')}/{method}"
            if count_only:
                # CountTokens input is an AWS JSON blob: the protocol serializes
                # InvokeModel's UTF-8 JSON body using base64.
                import base64

                payload["max_tokens"] = getattr(message, "max_tokens", self.limits.max_output_tokens)

                prompt = json.dumps(payload, separators=(",", ":")).encode()
                payload = {"input": {"invokeModel": {"body": base64.b64encode(prompt).decode()}}}
        return url, json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()

    @asynccontextmanager
    async def _upstream(self, message, *, count_only, before_transport):
        url, raw = self._request(message, count_only=count_only)
        try:
            headers = await self.credentials.headers(self.target, url=url, body=raw)
            deadline = datetime.fromisoformat(await before_transport())
            if datetime.now(UTC) + timedelta(seconds=4) >= deadline:
                raise ValueError("transport lease expired")
        except Exception:
            raise NoBillableEffect("provider.transport_lease_unavailable", count_only=count_only) from None
        async with self.client.stream("POST", url, content=raw, headers=headers) as response:
            if response.status_code != 200:
                # Do not reflect provider errors or automatically retry a call
                # that might already have incurred an effect.
                raise ContractError("provider.unavailable")
            if response.headers.get("content-encoding", "identity") != "identity":
                raise ContractError("provider.encoding_unsupported")
            yield response

    async def _json(self, response):
        raw = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=16_384):
            if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ContractError("provider.response_limit")
            raw.extend(chunk)
        return strict_json(bytes(raw), limit=MAX_RESPONSE_BYTES)

    async def _count(self, message, before_transport):
        async with self._upstream(message, count_only=True, before_transport=before_transport) as response:
            value = await self._json(response)
        key = "input_tokens" if self.target.provider == "vertex-v1" else "inputTokens"
        tokens = value.get(key)
        if type(tokens) is not int or not 0 <= tokens <= self.target.context_window_tokens:
            raise ContractError("provider.invalid_count")
        return tokens

    @asynccontextmanager
    async def invoke(self, message, *, count_only, before_transport):
        # The count is covered by the request's existing reservation and lease.
        # It cannot mint a second grant or bypass rate/concurrency admission.
        try:
            tokens = await self._count(message, before_transport)
        except Exception:
            # The fixed count endpoint is free, and no invocation was attempted.
            raise NoBillableEffect("provider.count_unavailable", count_only=count_only) from None
        if tokens > self.limits.max_input_tokens:
            raise NoBillableEffect("messages.input_limit", count_only=count_only)
        if not count_only and tokens + message.max_tokens > self.target.context_window_tokens:
            raise NoBillableEffect("messages.context_limit", count_only=False)
        if count_only:

            async def counted():
                yield json.dumps({"input_tokens": tokens}).encode()

            yield ProviderResponse(
                "application/json",
                counted(),
                ProviderUsage(items=(VerifiedUsage(component="request", units=0, provider_verified=True),)),
            )
            return
        async with self._upstream(message, count_only=False, before_transport=before_transport) as response:
            result = ProviderResponse("text/event-stream" if message.stream else "application/json", None)

            async def chunks():
                if message.stream:
                    tracker = StreamUsage()
                    decoder = vertex_events if self.target.provider == "vertex-v1" else bedrock_events
                    async for event in decoder(response.aiter_bytes(chunk_size=16_384)):
                        tracker.observe(event)
                        yield encode_sse(event)
                    result.usage = tracker.result()
                else:
                    value = await self._json(response)
                    result.usage = usage_from_message(value)
                    yield json.dumps(value, separators=(",", ":")).encode()

            result.chunks = chunks()
            yield result
