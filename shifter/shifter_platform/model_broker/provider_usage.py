"""Bounded provider output parsing and metadata-only usage normalization."""

import base64
import json
from collections.abc import AsyncIterable, AsyncIterator

from botocore.eventstream import EventStreamBuffer

from shared.model_access import ContractError
from shared.model_access.core_models import BillingComponent
from shared.model_access.messages import JsonObject, strict_json
from shared.model_access.provider import ProviderUsage, VerifiedUsage

_MAX_EVENT = 1_048_576
_INVALID_STREAM = "provider.invalid_stream"


def _units(value: object) -> int:
    """Accept bounded nonnegative provider usage counters without coercion."""
    if type(value) is not int or not 0 <= value <= 2_000_000:
        raise ContractError("provider.invalid_usage")
    return value


def usage_from_message(value: JsonObject) -> ProviderUsage:
    """Conservatively price cache reads; undeclared cache writes cannot settle."""
    usage = value.get("usage", {})
    if _units(usage.get("cache_creation_input_tokens", 0)):
        raise ContractError("provider.undeclared_cache_write")
    input_units = sum(
        _units(usage.get(key, 0)) for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )
    if "input_tokens" not in usage or "output_tokens" not in usage:
        raise ContractError("provider.incomplete_usage")
    return ProviderUsage(
        items=(
            VerifiedUsage(component=BillingComponent.INPUT_TOKENS, units=input_units, provider_verified=True),
            VerifiedUsage(
                component=BillingComponent.OUTPUT_TOKENS, units=_units(usage["output_tokens"]), provider_verified=True
            ),
        )
    )


class StreamUsage:
    """Require a complete Messages stream before releasing any conservative hold."""

    def __init__(self) -> None:
        self.input_usage: JsonObject | None = None
        self.output_tokens: int | None = None
        self.stopped = False

    def observe(self, event: JsonObject) -> None:
        """Track one ordered event and reject events after terminal completion."""
        kind = event.get("type")
        if self.stopped:
            raise ContractError(_INVALID_STREAM)
        if kind == "message_start":
            if self.input_usage is not None:
                raise ContractError(_INVALID_STREAM)
            self.input_usage = event.get("message", {}).get("usage", {})
        elif kind == "message_delta":
            self.output_tokens = event.get("usage", {}).get("output_tokens")
        elif kind == "message_stop":
            self.stopped = True
        elif kind not in {"content_block_start", "content_block_delta", "content_block_stop", "ping"}:
            raise ContractError(_INVALID_STREAM)

    def result(self) -> ProviderUsage:
        """Return billable usage only after both counters and stream completion."""
        if not self.stopped or self.input_usage is None or self.output_tokens is None:
            raise ContractError("provider.incomplete_usage")
        return usage_from_message({"usage": {**self.input_usage, "output_tokens": self.output_tokens}})


async def vertex_events(chunks: AsyncIterable[bytes]) -> AsyncIterator[JsonObject]:
    """Reframe complete SSE events so partial frames cannot bypass usage checks."""
    pending = bytearray()
    async for chunk in chunks:
        pending.extend(chunk)
        if len(pending) > _MAX_EVENT:
            raise ContractError("provider.event_limit")
        while b"\n\n" in pending or b"\r\n\r\n" in pending:
            delimiter = b"\r\n\r\n" if b"\r\n\r\n" in pending else b"\n\n"
            raw, _, remaining = pending.partition(delimiter)
            pending = bytearray(remaining)
            lines = raw.replace(b"\r", b"").splitlines()
            data = b"\n".join(line[5:].lstrip() for line in lines if line.startswith(b"data:"))
            if data:
                yield strict_json(data, limit=_MAX_EVENT)
    if pending.strip():
        raise ContractError("provider.truncated_stream")


async def bedrock_events(chunks: AsyncIterable[bytes]) -> AsyncIterator[JsonObject]:
    """Decode CRC-checked AWS event frames without retaining an unbounded buffer."""
    buffer = EventStreamBuffer()
    pending_bytes = 0
    async for chunk in chunks:
        pending_bytes += len(chunk)
        if pending_bytes > _MAX_EVENT:
            raise ContractError("provider.event_limit")
        buffer.add_data(chunk)
        for event in buffer:
            pending_bytes -= event.prelude.total_length
            if event.headers.get(":message-type") != "event" or event.headers.get(":event-type") != "chunk":
                raise ContractError("provider.stream_error")
            payload = strict_json(event.payload, limit=_MAX_EVENT)
            try:
                raw = base64.b64decode(payload["bytes"], validate=True)
            except (ValueError, KeyError, TypeError):
                raise ContractError(_INVALID_STREAM) from None
            yield strict_json(raw, limit=_MAX_EVENT)
    if pending_bytes:
        raise ContractError("provider.truncated_stream")


def encode_sse(event: JsonObject) -> bytes:
    """Encode one validated provider event in the guest SSE transport."""
    return (
        b"event: "
        + event["type"].encode("ascii")
        + b"\ndata: "
        + json.dumps(event, separators=(",", ":")).encode()
        + b"\n\n"
    )
