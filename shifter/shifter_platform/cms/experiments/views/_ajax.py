"""AJAX endpoints for the experiment manager.

HTTP-only: parse the request, call ``cms.experiments.services``, return JSON.
All business logic lives in the service layer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from django.http import JsonResponse

from cms.experiments import services
from cms.experiments.exceptions import ExperimentValidationError
from shared.auth import threat_research_required
from shared.errors import classify_user_message
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


@threat_research_required
def scenario_instances(request: HttpRequest, scenario_id: str) -> JsonResponse:
    """Return instance list for a scenario (AJAX)."""
    logger.info("scenario_instances: user_id=%s scenario_id=%s", request.user.id, safe_log_value(scenario_id))
    try:
        instances = services.get_scenario_instances(scenario_id, user=cast("User", request.user))
        return JsonResponse({"instances": instances})
    except ExperimentValidationError as e:
        logger.exception("scenario_instances: validation error for scenario_id=%s", safe_log_value(scenario_id))
        return JsonResponse({"error": classify_user_message(str(e), default="Invalid scenario request")}, status=400)
    except Exception:
        logger.exception("scenario_instances: unexpected error")
        return JsonResponse({"error": "An unexpected error occurred."}, status=500)
