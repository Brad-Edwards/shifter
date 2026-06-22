"""Download views for experiment bundles and individual artifacts.

HTTP-only: parse the request, call ``cms.experiments.services``, redirect to
the presigned URL. All business logic lives in the service layer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect

from cms.experiments import services
from cms.experiments.exceptions import ArtifactError
from shared.auth import threat_research_required
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


@threat_research_required
def experiment_download(request: HttpRequest, experiment_id: int) -> HttpResponse:
    """Redirect to presigned download URL for experiment bundle."""
    logger.info("experiment_download: user_id=%s experiment_id=%s", request.user.id, safe_log_value(experiment_id))
    try:
        url = services.get_bundle_download_url(cast("User", request.user), experiment_id)
        return redirect(url)
    except ArtifactError as e:
        messages.error(request, str(e))
        return redirect("experiments:experiment_detail", experiment_id=experiment_id)
    except Exception:
        logger.exception(
            "experiment_download: unexpected error for user_id=%s",
            request.user.id,
        )
        messages.error(request, "An unexpected error occurred. Please try again.")
        return redirect("experiments:experiment_list")


@threat_research_required
def artifact_download(
    request: HttpRequest,
    experiment_id: int,
    run_number: int,
    artifact_id: int,
) -> HttpResponse:
    """Redirect to presigned download URL for a single artifact."""
    logger.info(
        "artifact_download: user_id=%s experiment_id=%s artifact_id=%s",
        request.user.id,
        safe_log_value(experiment_id),
        safe_log_value(artifact_id),
    )
    try:
        url = services.get_artifact_download_url(cast("User", request.user), experiment_id, artifact_id)
        return redirect(url)
    except ArtifactError as e:
        messages.error(request, str(e))
        return redirect("experiments:experiment_detail", experiment_id=experiment_id)
    except Exception:
        logger.exception(
            "artifact_download: unexpected error for user_id=%s",
            request.user.id,
        )
        messages.error(request, "An unexpected error occurred. Please try again.")
        return redirect("experiments:experiment_list")
