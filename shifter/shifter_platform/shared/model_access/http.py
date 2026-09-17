"""Small bounded ASGI transport helpers shared by two private listeners."""

import asyncio
import json

from shared.model_access import ContractError


def headers(scope: dict) -> dict[str, str]:
    """Reject ambiguous duplicate security headers and compressed request bodies."""
    result = {}
    for name, value in scope.get("headers", ()):
        key = name.decode("ascii").lower()
        text = value.decode("ascii")
        if key in result or len(text) > 16_384:
            raise ContractError("http.invalid_headers")
        result[key] = text
    if result.get("content-encoding", "identity") != "identity":
        raise ContractError("http.encoding_unsupported")
    if "content-length" in result and "transfer-encoding" in result:
        raise ContractError("http.invalid_headers")
    return result


async def body(receive, *, limit: int, timeout: float = 5) -> bytes:
    """Reject chunked overflow and slow/disconnected uploads before parsing."""
    value = bytearray()
    async with asyncio.timeout(timeout):
        while True:
            message = await receive()
            if message["type"] != "http.request":
                raise ContractError("http.disconnected")
            chunk = message.get("body", b"")
            if len(value) + len(chunk) > limit:
                raise ContractError("http.too_large")
            value.extend(chunk)
            if not message.get("more_body", False):
                return bytes(value)


async def json_response(send, status: int, value: dict) -> None:
    """No cache or reflected upstream headers; callers supply safe error codes."""
    payload = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
