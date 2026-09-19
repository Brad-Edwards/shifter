"""One broker request's reservation, streaming and terminal accounting lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from typing import NoReturn
from uuid import uuid4

from shared.model_access import ContractError
from shared.model_access.credentials import ModelAccessAuthorization
from shared.model_access.http import Receive, Send
from shared.model_access.messages import MAX_RESPONSE_BYTES, CountTokensRequest, JsonObject, MessagesRequest
from shared.model_access.provider import ProviderUsage

from .control import ControlClient
from .errors import NoBillableEffect
from .providers import MessagesProvider

_RESPONSE_BODY = "http.response.body"


@dataclass(frozen=True)
class BrokerRequest:
    """Authenticated request facts retained through one provider invocation."""

    message: CountTokensRequest
    authority: ModelAccessAuthorization
    token: str
    peer: str
    count_only: bool
    retry_fields: JsonObject
    deadline: float


class BrokerInvocation:
    """Settle verified usage once; interrupted billable work retains its hold."""

    def __init__(
        self,
        control: ControlClient,
        provider: MessagesProvider,
        request: BrokerRequest,
        receive: Receive,
        send: Send,
        *,
        drain: asyncio.Event,
    ) -> None:
        self.control, self.provider, self.request = control, provider, request
        self.receive, self.send = receive, send
        self.drain = drain
        self.request_uuid = str(uuid4())
        self.identity = {"token": request.token, "transport_peer": request.peer, "request_uuid": self.request_uuid}
        self.dispatched = False
        self.started = False
        self.settled = False
        self.tasks: list[asyncio.Task[object]] = []

    async def run(self) -> None:
        """Reserve before dispatch and finalize accounting even on disconnect."""
        try:
            async with asyncio.timeout_at(self.request.deadline):
                if not await self._reserve():
                    return
                await self._dispatch()
                await self._settle(await self._wait_for_transfer())
                await self.send({"type": _RESPONSE_BODY, "body": b"", "more_body": False})
        except NoBillableEffect as exc:
            await self._settle(exc.usage)
            raise
        except Exception:
            await self._cancel_transport()
            if not self.started:
                raise
            await self._interrupted()
        finally:
            await self._finalize()

    async def _reserve(self) -> bool:
        """Deduplicate before any potentially billable provider work starts."""
        bounded = self.provider.message_billing_bound(self.request.message, count_only=self.request.count_only)
        reserved = await self.control.call(
            "reserve",
            {
                **self.identity,
                "logical_alias": self.request.message.model,
                "billing_bound": bounded.model_dump(mode="json"),
                **self.request.retry_fields,
            },
        )
        if reserved["request_uuid"] != self.request_uuid:
            from shared.model_access.http import json_response

            await json_response(self.send, 409, {"type": "completed", "request_id": reserved["request_uuid"]})
            return False
        return True

    async def _dispatch(self) -> None:
        """Fence provider dispatch with a fresh, checked accounting lease."""
        lease = await self.control.call("advance", {**self.identity, "action": "dispatch"})
        self.dispatched = True
        await self.control.call(
            "advance", {**self.identity, "action": "check", "dispatch_token": lease["dispatch_token"]}
        )

    async def _wait_for_transfer(self) -> ProviderUsage:
        """End the transfer when either peer disconnects or its grant is revoked."""
        work = asyncio.create_task(self._transfer())
        heartbeat = asyncio.create_task(self._renew())
        disconnect = asyncio.create_task(self._disconnected())
        draining = asyncio.create_task(self._drained())
        self.tasks = [work, heartbeat, disconnect, draining]
        done, _ = await asyncio.wait(self.tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in (heartbeat, disconnect, draining):
            if task in done:
                task.result()
        return work.result()

    async def _settle(self, usage: ProviderUsage) -> None:
        """Mark settlement only after Engine accepts the verified usage."""
        await self.control.call(
            "finish", {"request_uuid": self.request_uuid, "action": "settle", "usage": usage.model_dump(mode="json")}
        )
        self.settled = True

    async def _interrupted(self) -> None:
        """Close a started stream with a fixed error and no upstream diagnostics."""
        payload = b""
        if isinstance(self.request.message, MessagesRequest) and self.request.message.stream:
            payload = (
                b'\n\nevent: error\ndata: {"type":"error",'
                b'"error":{"type":"api_error","message":"broker.interrupted"}}\n\n'
            )
        with suppress(OSError, TimeoutError):
            async with asyncio.timeout(1):
                await self.send(
                    {
                        "type": _RESPONSE_BODY,
                        "body": payload,
                        "more_body": False,
                    }
                )

    async def _finalize(self) -> None:
        """Cancel sibling tasks and preserve uncertain provider liability."""
        await self._cancel_transport()
        if not self.settled:
            with suppress(Exception):
                await self.control.call(
                    "finish", {"request_uuid": self.request_uuid, "action": "unknown" if self.dispatched else "release"}
                )

    async def _cancel_transport(self) -> None:
        """Fence upstream before attempting any downstream error notification."""
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task

    async def _before_transport(self) -> str:
        """Recheck authority immediately before every upstream HTTP operation."""
        result = await self.control.call("advance", {**self.identity, "action": "continue"})
        return str(result["deadline"])

    async def _transfer(self) -> ProviderUsage:
        """Forward bounded chunks and require usage before terminal success."""
        total = 0
        async with self.provider.invoke(
            self.request.message, count_only=self.request.count_only, before_transport=self._before_transport
        ) as response:
            await self.send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", response.content_type.encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-request-id", self.request_uuid.encode()),
                    ],
                }
            )
            self.started = True
            async for chunk in response.chunks:
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise ContractError("provider.response_limit")
                await self.send({"type": _RESPONSE_BODY, "body": chunk, "more_body": True})
            if response.usage is None:
                raise ContractError("provider.incomplete_usage")
            return response.usage

    async def _renew(self) -> None:
        """Keep streaming authority checked until completion or cancellation."""
        while True:
            await asyncio.sleep(2)
            await self.control.call("advance", {**self.identity, "action": "continue"})

    async def _disconnected(self) -> NoReturn:
        """Stop provider transport when the participant disconnects."""
        while True:
            if (await self.receive())["type"] == "http.disconnect":
                raise ContractError("http.disconnected")

    async def _drained(self) -> NoReturn:
        """Terminate transport as soon as process drain starts."""
        await self.drain.wait()
        raise ContractError("broker.draining")
