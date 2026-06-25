"""Standard DRF error envelope for the platform API."""

from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from shared.errors import classify_user_message, safe_user_message

_STATUS_MESSAGES = {
    status.HTTP_400_BAD_REQUEST: "Invalid request",
    status.HTTP_401_UNAUTHORIZED: "Authentication failed",
    status.HTTP_403_FORBIDDEN: "Permission denied",
    status.HTTP_404_NOT_FOUND: "Resource not found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "Method not allowed",
    status.HTTP_429_TOO_MANY_REQUESTS: "Request was throttled",
}


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Wrap DRF exceptions in the platform API error envelope."""
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    error: dict[str, Any] = {
        "code": _error_code(exc),
        "message": _error_message(exc, response),
    }
    if isinstance(exc, ValidationError):
        error["details"] = _normalize_detail(response.data)
    request_id = _request_id(context.get("request"))
    if request_id:
        error["request_id"] = request_id

    response.data = {"error": error}
    return response


def api_error_response(
    *,
    code: str,
    message: str,
    status_code: int,
    details: Any | None = None,
    request: Any | None = None,
) -> Response:
    """Return an explicit API error using the same envelope as DRF exceptions."""
    error: dict[str, Any] = {
        "code": code,
        "message": safe_user_message(message),
    }
    if details is not None:
        error["details"] = _normalize_detail(details)
    request_id = _request_id(request)
    if request_id:
        error["request_id"] = request_id
    return Response({"error": error}, status=status_code)


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "default_code", None)
    if isinstance(code, str) and code:
        return code
    return "api_error"


def _error_message(exc: Exception, response: Response) -> str:
    if isinstance(exc, ValidationError):
        return _STATUS_MESSAGES[status.HTTP_400_BAD_REQUEST]
    if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
        return _STATUS_MESSAGES[response.status_code]

    default = _STATUS_MESSAGES.get(response.status_code, "Request could not be processed")
    return classify_user_message(_extract_detail(response.data), default=default)


def _extract_detail(data: Any) -> object:
    if isinstance(data, dict) and "detail" in data:
        return data["detail"]
    if isinstance(data, list):
        return " ".join(str(item) for item in data)
    return data


def _normalize_detail(data: Any) -> Any:
    if isinstance(data, ErrorDetail):
        return str(data)
    if isinstance(data, dict):
        return {key: _normalize_detail(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_normalize_detail(item) for item in data]
    return data


def _request_id(request: Any | None) -> str | None:
    if request is None:
        return None
    request_id = getattr(request, "request_id", None)
    if request_id:
        return str(request_id)
    meta = getattr(request, "META", {})
    request_id = meta.get("HTTP_X_REQUEST_ID") if isinstance(meta, dict) else None
    return str(request_id) if request_id else None
