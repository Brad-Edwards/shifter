"""Vertex and Bedrock invocation through fixed, deployment-approved origins."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import quote

import httpx

from shared.model_access import BillingBound, ContractError
from shared.model_access.core_models import AccessLimits, BillingComponent, ModelShard
from shared.model_access.messages import (
    MAX_RESPONSE_BYTES,
    CountTokensRequest,
    JsonObject,
    MessagesRequest,
    strict_json,
)
from shared.model_access.provider import (
    BillingAmount,
    CancellationResult,
    ModelProviderAdapter,
    ProviderAdapterRegistry,
    ProviderCapabilities,
    ProviderUsage,
    VerifiedUsage,
)
from shared.model_access.provider_runtime import ProviderInventory, ProviderTarget

from .errors import NoBillableEffect
from .provider_credentials import ProviderCredentials
from .provider_usage import StreamUsage, bedrock_events, encode_sse, usage_from_message, vertex_events


@dataclass
class ProviderResponse:
    """Streaming content plus usage populated only after a complete upstream reply."""

    content_type: str
    chunks: AsyncIterator[bytes]
    usage: ProviderUsage | None = None


class ProviderRegistry:
    """Exact shard inventory; no provider/model fallback on failure."""

    def __init__(
        self, *, inventory: ProviderInventory, credentials: ProviderCredentials, client: httpx.AsyncClient | None = None
    ) -> None:
        self.targets = {target.shard_id: target for target in inventory.targets}
        self.credentials = credentials
        self.client = client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(5, connect=1, pool=0.25, write=1),
            limits=httpx.Limits(max_connections=128),
        )

    def build(self, shard: ModelShard, limits: AccessLimits) -> MessagesProvider:
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
        return cast(MessagesProvider, registry.build(shard))

    async def close(self) -> None:
        await self.client.aclose()


class MessagesProvider(ModelProviderAdapter):
    """Text/local-tool protocol with conservative full-context spend reservation."""

    def __init__(
        self,
        *,
        target: ProviderTarget,
        limits: AccessLimits,
        credentials: ProviderCredentials,
        client: httpx.AsyncClient,
    ) -> None:
        self.target, self.limits, self.credentials, self.client = target, limits, credentials, client

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            adapter_id=self.target.provider,
            protocols=("anthropic-messages/2023-06-01",),
            models=(self.target.model,),
            capabilities=("messages", "token-count"),
            billing_components=(
                BillingComponent.INPUT_TOKENS,
                BillingComponent.OUTPUT_TOKENS,
                BillingComponent.REQUEST,
            ),
            trustworthy_usage_components=(
                BillingComponent.INPUT_TOKENS,
                BillingComponent.OUTPUT_TOKENS,
                BillingComponent.REQUEST,
            ),
            streaming=True,
            token_counting=True,
            cancellation=False,
            completion_horizon_seconds=900,
        )

    @staticmethod
    def normalize_usage(provider_result: object) -> ProviderUsage:
        if not isinstance(provider_result, dict):
            raise ContractError("provider.invalid_usage")
        return usage_from_message(provider_result)

    @staticmethod
    def cancel(provider_request_id: str) -> CancellationResult:
        # Closing a stream ends our transport; it does not prove a provider job
        # stopped. Preserve the conservative liability through reconciliation.
        from shared.model_access.provider import CancellationDisposition

        return CancellationResult(disposition=CancellationDisposition.UNSUPPORTED)

    def billing_bound(
        self,
        message: CountTokensRequest | None = None,
        *,
        count_only: bool = False,
        model: str | None = None,
        features: tuple[str, ...] = (),
        request_bytes: int = 0,
    ) -> BillingBound:
        if model is not None and (model != self.target.model or set(features) - {"messages", "token-count"}):
            raise ContractError("provider.capability_mismatch")
        if request_bytes > self.limits.max_request_bytes:
            raise ContractError("messages.too_large")
        count_only = count_only or features == ("token-count",)

        # Count endpoints are free but still consume rate/concurrency. The
        # catalog carries a zero-price `request` component for this operation.
        amounts = (
            [(BillingComponent.REQUEST, 1)]
            if count_only
            else [
                (BillingComponent.INPUT_TOKENS, self.target.context_window_tokens),
                (
                    BillingComponent.OUTPUT_TOKENS,
                    message.max_tokens if isinstance(message, MessagesRequest) else self.limits.max_output_tokens,
                ),
            ]
        )
        return BillingBound(
            amounts=tuple(
                BillingAmount(component=key, units=value, maximum_charge_micro_units=0) for key, value in amounts
            )
        )

    def _request(self, message: CountTokensRequest, *, count_only: bool) -> tuple[str, bytes]:
        payload = message.model_dump(mode="json", exclude_none=True)
        payload.pop("model", None)
        if count_only:
            for field in ("max_tokens", "stream", "temperature", "top_p", "top_k", "stop_sequences"):
                payload.pop(field, None)
        if self.target.provider == "vertex-v1":
            url = _vertex_request(self.target, payload, count_only=count_only)
        else:
            output_limit = message.max_tokens if isinstance(message, MessagesRequest) else self.limits.max_output_tokens
            url, payload = _bedrock_request(self.target, payload, count_only=count_only, output_limit=output_limit)
        return url, json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()

    @asynccontextmanager
    async def _upstream(
        self, message: CountTokensRequest, *, count_only: bool, before_transport: Callable[[], Awaitable[str]]
    ) -> AsyncIterator[httpx.Response]:
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

    @staticmethod
    async def _json(response: httpx.Response) -> JsonObject:
        raw = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=16_384):
            if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ContractError("provider.response_limit")
            raw.extend(chunk)
        return strict_json(bytes(raw), limit=MAX_RESPONSE_BYTES)

    async def _count(self, message: CountTokensRequest, before_transport: Callable[[], Awaitable[str]]) -> int:
        async with self._upstream(message, count_only=True, before_transport=before_transport) as response:
            value = await self._json(response)
        key = "input_tokens" if self.target.provider == "vertex-v1" else "inputTokens"
        tokens = value.get(key)
        if type(tokens) is not int or not 0 <= tokens <= self.target.context_window_tokens:
            raise ContractError("provider.invalid_count")
        return tokens

    @asynccontextmanager
    async def invoke(
        self, message: CountTokensRequest, *, count_only: bool, before_transport: Callable[[], Awaitable[str]]
    ) -> AsyncIterator[ProviderResponse]:
        # The count is covered by the request's existing reservation and lease.
        # It cannot mint a second grant or bypass rate/concurrency admission.
        try:
            tokens = await self._count(message, before_transport)
        except Exception:
            # The fixed count endpoint is free, and no invocation was attempted.
            raise NoBillableEffect("provider.count_unavailable", count_only=count_only) from None
        if tokens > self.limits.max_input_tokens:
            raise NoBillableEffect("messages.input_limit", count_only=count_only)
        if not count_only:
            if not isinstance(message, MessagesRequest):
                raise NoBillableEffect("messages.unsupported_request", count_only=False)
            if tokens + message.max_tokens > self.target.context_window_tokens:
                raise NoBillableEffect("messages.context_limit", count_only=False)
        if count_only:

            async def counted() -> AsyncIterator[bytes]:
                """Return only the normalized free token count."""
                yield json.dumps({"input_tokens": tokens}).encode()

            yield ProviderResponse(
                "application/json",
                counted(),
                ProviderUsage(
                    items=(VerifiedUsage(component=BillingComponent.REQUEST, units=0, provider_verified=True),)
                ),
            )
            return
        async with self._upstream(message, count_only=False, before_transport=before_transport) as response:
            assert isinstance(message, MessagesRequest)

            async def chunks() -> AsyncIterator[bytes]:
                """Normalize each response and publish usage only at completion."""
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

            result = ProviderResponse("text/event-stream" if message.stream else "application/json", chunks())
            yield result


def _vertex_request(target: ProviderTarget, payload: JsonObject, *, count_only: bool) -> str:
    """Bind a Vertex request to its configured region, project and publisher model."""
    region = target.count_region if count_only else target.region
    model = "count-tokens" if count_only else target.model.rsplit("/", 1)[1]
    method = "streamRawPredict" if payload.get("stream") else "rawPredict"
    if count_only:
        payload["model"] = target.model.rsplit("/", 1)[1]
    else:
        payload["anthropic_version"] = "vertex-2023-10-16"
    return (
        f"https://{region}-aiplatform.googleapis.com/v1/projects/{target.project}/locations/{region}"
        f"/publishers/anthropic/models/{model}:{method}"
    )


def _bedrock_request(
    target: ProviderTarget, payload: JsonObject, *, count_only: bool, output_limit: int
) -> tuple[str, JsonObject]:
    """Encode the fixed Bedrock invocation or free CountTokens request."""
    stream = bool(payload.pop("stream", False))
    payload["anthropic_version"] = "bedrock-2023-05-31"
    if count_only:
        method = "count-tokens"
        payload["max_tokens"] = output_limit
        prompt = json.dumps(payload, separators=(",", ":")).encode()
        payload = {"input": {"invokeModel": {"body": base64.b64encode(prompt).decode()}}}
    else:
        method = "invoke-with-response-stream" if stream else "invoke"
    url = f"https://bedrock-runtime.{target.region}.amazonaws.com/model/{quote(target.model, safe='')}/{method}"
    return url, payload
