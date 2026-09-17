"""TLS control ASGI application with separate broker/provisioner authority."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from asgiref.sync import sync_to_async
from pydantic import ValidationError

from engine.services import (
    authenticate_model_access,
    exchange_model_enrollment,
    issue_model_enrollment,
    refresh_model_access,
)
from engine.services._model_broker_control import advance_model_call, finish_model_call, reserve_model_call
from shared.model_access import ContractError
from shared.model_access.http import body, headers, json_response
from shared.model_access.messages import strict_json

from .schemas import AdvanceRequest, EnrollmentRequest, FinishRequest, ReservationRequest, TokenRequest


class ControlApplication:
    """Only authenticated workload callers can reach Engine effects."""

    def __init__(self, *, verify_identity, ready):
        self.verify_identity = verify_identity
        self.ready = ready

    async def __call__(self, scope, receive, send):
        try:
            if scope["type"] != "http":
                return
            path = scope.get("path", "")
            if scope.get("query_string"):
                raise ContractError("control.invalid_route")
            if scope["method"] == "GET" and path in {"/health/live", "/health/ready"}:
                healthy = path.endswith("live") or await sync_to_async(self.ready)()
                await json_response(send, 200 if healthy else 503, {"ready": healthy})
                return
            allowed = {"enroll", "exchange", "refresh", "authenticate", "reserve", "advance", "finish"}
            route = path.removeprefix("/control/v1/")
            if scope["method"] != "POST" or route not in allowed or path != f"/control/v1/{route}":
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
        except ContractError as exc:
            status = 401 if exc.code in {"control.unauthorized", "credential.unavailable"} else 409
            await json_response(send, status, {"error": exc.code})
        except (ValidationError, ValueError, TimeoutError, UnicodeError):
            await json_response(send, 400, {"error": "control.invalid_request"})
        except Exception:
            # No raw exception logging: request arguments can contain credentials.
            await json_response(send, 503, {"error": "control.unavailable"})

    @staticmethod
    def _dispatch(route: str, payload: dict) -> dict:
        if route == "enroll":
            enrollment_request = EnrollmentRequest.model_validate(payload)
            enrollment = issue_model_enrollment(**enrollment_request.model_dump())
            return {
                "grant_id": str(enrollment.grant_id),
                "enrollment_token": enrollment.enrollment_token.get_secret_value(),
                "expires_at": enrollment.expires_at.isoformat(),
            }
        if route in {"exchange", "refresh", "authenticate"}:
            token_request = TokenRequest.model_validate(payload)
            token = token_request.token.get_secret_value()
            peer = token_request.transport_peer
            if route == "authenticate":
                authority = authenticate_model_access(token=token, transport_peer=peer)
                return authority.model_dump(mode="json")
            operation = exchange_model_enrollment if route == "exchange" else refresh_model_access
            pair = operation(token=token, transport_peer=peer)
            return {
                "access_token": pair.access_token.get_secret_value(),
                "refresh_token": pair.refresh_token.get_secret_value(),
                "access_expires_at": pair.access_expires_at.isoformat(),
                "hard_expires_at": pair.hard_expires_at.isoformat(),
            }
        if route == "reserve":
            reservation_request = ReservationRequest.model_validate(payload)
            values = reservation_request.model_dump(exclude={"token", "billing_bound"})
            reservation = reserve_model_call(
                **values,
                token=reservation_request.token.get_secret_value(),
                billing_bound=reservation_request.billing_bound,
            )
            return {**asdict(reservation), "request_uuid": str(reservation.request_uuid)}
        if route == "advance":
            advance_request = AdvanceRequest.model_validate(payload)
            return advance_model_call(
                **advance_request.model_dump(exclude={"token", "dispatch_token"}),
                token=advance_request.token.get_secret_value(),
                dispatch_token=advance_request.dispatch_token.get_secret_value(),
            )
        finish_request = FinishRequest.model_validate(payload)
        return finish_model_call(
            request_uuid=finish_request.request_uuid, action=finish_request.action, usage=finish_request.usage
        )
