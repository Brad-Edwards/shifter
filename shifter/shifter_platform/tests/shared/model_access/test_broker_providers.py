"""Wire-level provider tests using real HTTP serialization and stream decoders."""

import base64
import json
import struct
import zlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from model_broker.provider_usage import StreamUsage, bedrock_events, usage_from_message, vertex_events
from model_broker.providers import MessagesProvider
from shared.model_access import ContractError
from shared.model_access.messages import parse_messages
from shared.model_access.provider_runtime import ProviderTarget
from tests.engine.services.test_model_request_accounting import _limits

pytestmark = pytest.mark.asyncio


def target(provider):
    return ProviderTarget(
        shard_id="synthetic",
        provider=provider,
        region="us-east-1" if provider == "bedrock-v1" else "us-east5",
        model="anthropic.claude-synthetic-v1:0"
        if provider == "bedrock-v1"
        else "publishers/anthropic/models/claude-synthetic@20260901",
        credential_reference="synthetic-identity",
        context_window_tokens=200_000,
        principal="arn:aws:iam::123456789012:role/model-test"
        if provider == "bedrock-v1"
        else "model-test@models-test.iam.gserviceaccount.com",
        project="" if provider == "bedrock-v1" else "models-test",
        count_region="" if provider == "bedrock-v1" else "us",
    )


class CredentialsPort:
    async def headers(self, target, *, url, body):
        return {"content-type": "application/json", "authorization": "synthetic-workload-proof"}


async def lease():
    return (datetime.now(UTC) + timedelta(seconds=5)).isoformat()


def message(*, stream=False):
    return parse_messages(
        json.dumps(
            {
                "model": "coding-main",
                "max_tokens": 20,
                "stream": stream,
                "messages": [{"role": "user", "content": "test"}],
            }
        ).encode(),
        count_only=False,
        limits=_limits(),
    )


@pytest.mark.parametrize("provider", ["vertex-v1", "bedrock-v1"])
async def test_fixed_provider_origins_count_before_invoke_and_normalize_usage(provider):
    calls = []

    def transport(request):
        calls.append(request)
        if "count-tokens" in request.url.path:
            return httpx.Response(200, json={"input_tokens" if provider == "vertex-v1" else "inputTokens": 12})
        return httpx.Response(
            200,
            json={
                "type": "message",
                "content": [{"type": "text", "text": "answer"}],
                "usage": {"input_tokens": 12, "output_tokens": 5},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        adapter = MessagesProvider(
            target=target(provider), limits=_limits(), credentials=CredentialsPort(), client=client
        )
        bound = adapter.billing_bound(message(), count_only=False)
        assert bound.amounts[0].units == 200_000  # full model context; no character-count estimate
        async with adapter.invoke(message(), count_only=False, before_transport=lease) as result:
            raw = b"".join([chunk async for chunk in result.chunks])
        assert json.loads(raw)["content"][0]["text"] == "answer"
        assert [(item.component, item.units) for item in result.usage.items] == [
            ("input_tokens", 12),
            ("output_tokens", 5),
        ]
    assert len(calls) == 2
    invoke = json.loads(calls[1].content)
    assert "model" not in invoke
    if provider == "vertex-v1":
        assert calls[0].url.host == "us-aiplatform.googleapis.com"
        assert calls[1].url.host == "us-east5-aiplatform.googleapis.com"
        assert invoke["anthropic_version"] == "vertex-2023-10-16"
    else:
        assert all(request.url.host == "bedrock-runtime.us-east-1.amazonaws.com" for request in calls)
        assert "stream" not in invoke
        prompt = json.loads(base64.b64decode(json.loads(calls[0].content)["input"]["invokeModel"]["body"]))
        assert prompt["messages"][0]["content"] == "test"
        assert invoke["anthropic_version"] == "bedrock-2023-05-31"


async def test_revocation_between_count_and_invoke_prevents_paid_transport():
    calls = []

    async def before_transport():
        if calls:
            raise ContractError("request.revoked")
        return await lease()

    def transport(request):
        calls.append(request)
        return httpx.Response(200, json={"input_tokens": 12})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        adapter = MessagesProvider(
            target=target("vertex-v1"), limits=_limits(), credentials=CredentialsPort(), client=client
        )
        with pytest.raises(ContractError):
            async with adapter.invoke(message(), count_only=False, before_transport=before_transport):
                pytest.fail("revoked invocation yielded a provider response")
    assert len(calls) == 1 and "count-tokens" in calls[0].url.path


async def test_short_lease_never_starts_provider_transport():
    async def expired():
        return (datetime.now(UTC) + timedelta(seconds=1)).isoformat()

    def forbidden(request):
        pytest.fail("expired lease reached network")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        adapter = MessagesProvider(
            target=target("vertex-v1"), limits=_limits(), credentials=CredentialsPort(), client=client
        )
        with pytest.raises(ContractError, match="count_unavailable"):
            async with adapter.invoke(message(), count_only=False, before_transport=expired):
                pytest.fail("expired lease returned a response")


def _aws_frame(event):
    def header(name, value):
        return bytes([len(name)]) + name.encode() + b"\x07" + struct.pack(">H", len(value)) + value.encode()

    headers = header(":message-type", "event") + header(":event-type", "chunk")
    payload = json.dumps({"bytes": base64.b64encode(json.dumps(event).encode()).decode()}).encode()
    prelude = struct.pack(">II", 16 + len(headers) + len(payload), len(headers))
    raw = prelude + struct.pack(">I", zlib.crc32(prelude)) + headers + payload
    return raw + struct.pack(">I", zlib.crc32(raw))


@pytest.mark.parametrize("provider", ["vertex-v1", "bedrock-v1"])
async def test_fragmented_stream_frames_require_complete_verified_usage(provider):
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 12}}},
        {"type": "message_delta", "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]
    if provider == "vertex-v1":
        raw = b"".join(b"data: " + json.dumps(event).encode() + b"\r\n\r\n" for event in events)
    else:
        raw = b"".join(_aws_frame(event) for event in events)

    async def chunks():
        for index in range(0, len(raw), 7):
            yield raw[index : index + 7]

    tracker = StreamUsage()
    decoder = vertex_events if provider == "vertex-v1" else bedrock_events
    observed = []
    async for event in decoder(chunks()):
        tracker.observe(event)
        observed.append(event)
    assert observed == events
    assert [item.units for item in tracker.result().items] == [12, 5]
    incomplete = StreamUsage()
    incomplete.observe(events[0])
    incomplete.observe(events[1])
    with pytest.raises(ContractError):
        incomplete.result()


async def test_corrupt_aws_crc_is_rejected():
    raw = bytearray(_aws_frame({"type": "message_stop"}))
    raw[-1] ^= 1

    async def chunks():
        yield bytes(raw)

    with pytest.raises(Exception, match="Checksum mismatch"):
        [event async for event in bedrock_events(chunks())]


async def test_undeclared_cache_write_cannot_be_refunded_as_ordinary_input():
    with pytest.raises(ContractError, match="undeclared_cache_write"):
        usage_from_message({"usage": {"input_tokens": 5, "output_tokens": 1, "cache_creation_input_tokens": 100}})


async def test_broker_entry_import_does_not_initialize_application_or_database():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(  # noqa: S603 - current interpreter and fixed, repository-owned import probe.
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(root)!r}); import model_broker.__main__; "
            "assert not {'django', 'psycopg', 'redis', 'config.settings', 'engine.models'} & sys.modules.keys()",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
