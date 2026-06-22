"""Script-asset views for the experiment manager.

HTTP-only: parse the request, call ``cms.experiments.services``, render or
redirect. All business logic lives in the service layer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from cms.experiments import services
from cms.experiments.exceptions import ScriptUploadError
from cms.experiments.views._constants import (
    ROUTE_EXPERIMENT_LIST,
    ROUTE_SCRIPT_LIST,
    ROUTE_SCRIPT_UPLOAD,
    UNEXPECTED_ERROR_MESSAGE,
)
from shared.auth import threat_research_required
from shared.errors import classify_user_message
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


@threat_research_required
def script_list(request: HttpRequest) -> HttpResponse:
    """List user's script assets."""
    logger.info("script_list: user_id=%s", request.user.id)
    try:
        scripts = services.list_scripts(cast("User", request.user))
        return render(
            request,
            "experiments/script_list.html",
            {
                "active_nav": "experiments",
                "scripts": scripts,
            },
        )
    except Exception:
        logger.exception(
            "script_list: unexpected error for user_id=%s",
            request.user.id,
        )
        messages.error(request, UNEXPECTED_ERROR_MESSAGE)
        return redirect(ROUTE_EXPERIMENT_LIST)


def _complete_script_upload_post(request: HttpRequest, upload_token: str) -> HttpResponse:
    """Finalize a presigned script upload the client has confirmed."""
    try:
        script = services.complete_script_upload(cast("User", request.user), upload_token)
    except ScriptUploadError as e:
        messages.error(request, str(e))
        return redirect(ROUTE_SCRIPT_UPLOAD)
    messages.success(request, f"Script '{script.name}' uploaded successfully.")
    return redirect(ROUTE_SCRIPT_LIST)


def _initiate_script_upload_post(request: HttpRequest) -> HttpResponse:
    """Start a presigned script upload and return the presigned URL as JSON."""
    name = request.POST.get("name", "").strip()
    filename = request.POST.get("filename", "").strip()
    try:
        file_size_int = int(request.POST.get("file_size", "0"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid file_size"}, status=400)
    try:
        result = services.initiate_script_upload(cast("User", request.user), name, filename, file_size_int)
    except ScriptUploadError as e:
        logger.exception("script_upload: initiation failed for user_id=%s", request.user.id)
        return JsonResponse(
            {"error": classify_user_message(str(e), default="Upload could not be initiated")}, status=400
        )
    return JsonResponse(result)


def _handle_script_upload_post(request: HttpRequest) -> HttpResponse:
    """Dispatch a script-upload POST to the completion or initiation path."""
    try:
        upload_token = request.POST.get("upload_token")
        if upload_token:
            return _complete_script_upload_post(request, upload_token)
        return _initiate_script_upload_post(request)
    except Exception:
        logger.exception("script_upload: unexpected error for user_id=%s", request.user.id)
        messages.error(request, UNEXPECTED_ERROR_MESSAGE)
        return redirect(ROUTE_EXPERIMENT_LIST)


@threat_research_required
def script_upload(request: HttpRequest) -> HttpResponse:
    """Upload a script file — two-step presigned URL flow.

    GET:  Show upload form.
    POST: Initiate upload (returns presigned URL via JSON).
    """
    logger.info("script_upload: user_id=%s method=%s", request.user.id, safe_log_value(request.method))
    if request.method == "GET":
        return render(request, "experiments/script_upload.html", {"active_nav": "experiments"})
    if request.method == "POST":
        return _handle_script_upload_post(request)
    return HttpResponse(status=405)


@threat_research_required
@require_POST
def script_delete(request: HttpRequest, script_id: int) -> HttpResponse:
    """Soft-delete a script."""
    logger.info("script_delete: user_id=%s script_id=%s", request.user.id, safe_log_value(script_id))
    try:
        services.delete_script(cast("User", request.user), script_id)
        messages.success(request, "Script deleted.")
    except ScriptUploadError as e:
        messages.error(request, str(e))
    except Exception:
        logger.exception(
            "script_delete: unexpected error for user_id=%s",
            request.user.id,
        )
        messages.error(request, UNEXPECTED_ERROR_MESSAGE)
    return redirect(ROUTE_SCRIPT_LIST)
