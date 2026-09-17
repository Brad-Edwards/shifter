"""TLS control ASGI application with separate broker/provisioner authority."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from functools import partial
from typing import Protocol
from uuid import UUID

from asgiref.sync import sync_to_async

from engine.services import (
    RequestIdempotency,
    authenticate_model_access,
    exchange_model_enrollment,
    issue_model_enrollment,
    refresh_model_access,
)
from engine.services._model_broker_control import advance_model_call, finish_model_call, reserve_model_call
from shared.model_access import ContractError
from shared.model_access.http import ASGIScope, Receive, Send, body, headers, json_response
from shared.model_access.messages import JsonObject, strict_json

from .schemas import AdvanceRequest, EnrollmentRequest, FinishRequest, ReservationRequest, TokenRequest


class IdentityVerifier(Protocol):
    """Verify broker identity or the provisioner audience for one enrollment."""

    def __call__(self, assertion: str, *, operation_id: UUID | None) -> None: ...


class ControlApplication:
    """Only authenticated workload callers can reach Engine effects."""

    def __init__(self, *, verify_identity: IdentityVerifier, ready: Callable[[], bool]) -> None:
        self.verify_identity = verify_identity
        self.ready = ready

    async def __call__(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        try:
            if scope["type"] != "http":
                return
            await self._route(scope, receive, send)
        except ContractError as exc:
            status = 401 if exc.code in {"control.unauthorized", "credential.unavailable"} else 409
            await json_response(send, status, {"error": exc.code})
        except (ValueError, TimeoutError):
            await json_response(send, 400, {"error": "control.invalid_request"})
        except Exception:
            # No raw exception logging: request arguments can contain credentials.
            await json_response(send, 503, {"error": "control.unavailable"})

    async def _route(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        """Serve private health probes or pass a query-free control request onward."""
        path = scope.get("path", "")
        if scope.get("query_string"):
            raise ContractError("control.invalid_route")
        if scope["method"] == "GET" and path in {"/health/live", "/health/ready"}:
            healthy = path.endswith("live") or await sync_to_async(self.ready)()
            await json_response(send, 200 if healthy else 503, {"ready": healthy})
        else:
            await self._post(scope, receive, send)

    async def _post(self, scope: ASGIScope, receive: Receive, send: Send) -> None:
        """Validate a closed control request before authenticating its workload."""
        path = scope.get("path", "")
        route = path.removeprefix("/control/v1/")
        if scope["method"] != "POST" or route not in _HANDLERS or path != f"/control/v1/{route}":
            raise ContractError("control.invalid_route")
        request_headers = headers(scope)
        if request_headers.get("content-type", "").split(";")[0] != "application/json":
            raise ContractError("control.invalid_content_type")
        payload = strict_json(await body(receive, limit=65_536), limit=65_536)
        enrollment = EnrollmentRequest.model_validate(payload) if route == "enroll" else None
        await asyncio.to_thread(
            self.verify_identity,
            request_headers.get("authorization", ""),
            operation_id=enrollment.operation_id if enrollment else None,
        )
        result = await sync_to_async(self._dispatch)(route, payload)
        await json_response(send, 200, result)

    @staticmethod
    def _dispatch(route: str, payload: JsonObject) -> JsonObject:
        handler = _HANDLERS.get(route)
        if handler is None:
            raise ContractError("control.invalid_route")
        return handler(payload)


def _enroll(payload: JsonObject) -> JsonObject:
    """Issue a guest enrollment for the authenticated operation generation."""
    request = EnrollmentRequest.model_validate(payload)
    enrollment = issue_model_enrollment(**request.model_dump())
    return {
        "grant_id": str(enrollment.grant_id),
        "enrollment_token": enrollment.enrollment_token.get_secret_value(),
        "expires_at": enrollment.expires_at.isoformat(),
    }


def _token_operation(payload: JsonObject, *, route: str) -> JsonObject:
    """Exchange, refresh or authenticate a peer-bound guest credential."""
    request = TokenRequest.model_validate(payload)
    token, peer = request.token.get_secret_value(), request.transport_peer
    if route == "authenticate":
        return authenticate_model_access(token=token, transport_peer=peer).model_dump(mode="json")
    operation = exchange_model_enrollment if route == "exchange" else refresh_model_access
    pair = operation(token=token, transport_peer=peer)
    return {
        "access_token": pair.access_token.get_secret_value(),
        "refresh_token": pair.refresh_token.get_secret_value(),
        "access_expires_at": pair.access_expires_at.isoformat(),
        "hard_expires_at": pair.hard_expires_at.isoformat(),
    }


def _reserve(payload: JsonObject) -> JsonObject:
    """Reserve a provider billing bound through Engine's accounting boundary."""
    request = ReservationRequest.model_validate(payload)
    reservation = reserve_model_call(
        token=request.token.get_secret_value(),
        transport_peer=request.transport_peer,
        request_uuid=request.request_uuid,
        logical_alias=request.logical_alias,
        billing_bound=request.billing_bound,
        idempotency=RequestIdempotency(
            caller_key_hmac=request.caller_key_hmac,
            intent_fingerprint_hmac=request.intent_fingerprint_hmac,
            key_version=request.key_version,
            prior_caller_key_hmacs=request.prior_caller_key_hmacs,
            prior_intent_fingerprint_hmacs=request.prior_intent_fingerprint_hmacs,
            retained_key_versions=request.retained_key_versions,
        ),
    )
    return {**asdict(reservation), "request_uuid": str(reservation.request_uuid)}


def _advance(payload: JsonObject) -> JsonObject:
    """Advance only the request's authenticated dispatch lease."""
    request = AdvanceRequest.model_validate(payload)
    return advance_model_call(
        **request.model_dump(exclude={"token", "dispatch_token"}),
        token=request.token.get_secret_value(),
        dispatch_token=request.dispatch_token.get_secret_value(),
    )


def _finish(payload: JsonObject) -> JsonObject:
    """Settle usage or preserve uncertainty through the closed terminal actions."""
    request = FinishRequest.model_validate(payload)
    return finish_model_call(request_uuid=request.request_uuid, action=request.action, usage=request.usage)


_HANDLERS: dict[str, Callable[[JsonObject], JsonObject]] = {
    "enroll": _enroll,
    "exchange": partial(_token_operation, route="exchange"),
    "refresh": partial(_token_operation, route="refresh"),
    "authenticate": partial(_token_operation, route="authenticate"),
    "reserve": _reserve,
    "advance": _advance,
    "finish": _finish,
}
