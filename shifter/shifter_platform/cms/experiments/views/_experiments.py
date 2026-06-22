"""Experiment lifecycle views (list, create, detail, start, cancel).

HTTP-only: parse the request, call ``cms.experiments.services``, render or
redirect. All business logic lives in the service layer.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, cast

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from cms.experiments import services
from cms.experiments.exceptions import ExperimentError, ExperimentValidationError
from cms.experiments.schemas import ExperimentCreateInput
from shared.auth import threat_research_required
from shared.exceptions import CMSError
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


@threat_research_required
def experiment_list(request: HttpRequest) -> HttpResponse:
    """List user's experiments."""
    logger.info("experiment_list: user_id=%s", request.user.id)
    try:
        experiments_qs = services.list_experiments(cast("User", request.user))
        paginator = Paginator(experiments_qs, 25)
        page = paginator.get_page(request.GET.get("page"))
        return render(
            request,
            "experiments/experiment_list.html",
            {
                "active_nav": "experiments",
                "experiments": page,
            },
        )
    except Exception:
        logger.exception(
            "experiment_list: unexpected error for user_id=%s",
            request.user.id,
        )
        messages.error(request, "An unexpected error occurred. Please try again.")
        return redirect("experiments:experiment_list")


def _validate_experiment_create_input(request: HttpRequest) -> ExperimentCreateInput:
    """Parse and validate the experiment-create form into an ExperimentCreateInput.

    Raises ExperimentValidationError (with a user-facing message) on malformed
    JSON, missing fields, or Pydantic validation failures.
    """
    from cms.scenarios.registry import load_scenario_template
    from cms.scenarios.schema import ScenarioTemplate

    try:
        scripts_json = request.POST.get("scripts_json", "[]")
        scripts_data = json.loads(scripts_json) if scripts_json else []

        scenario_id = request.POST.get("scenario_id", "")
        try:
            scenario = load_scenario_template(scenario_id)
            if isinstance(scenario, ScenarioTemplate):
                instance_names = {inst.name for inst in scenario.instances}
            else:
                instance_names = set()
        except (ValueError, CMSError):
            instance_names = set()

        input_data = {
            "name": request.POST.get("name", ""),
            "description": request.POST.get("description", ""),
            "scenario_id": scenario_id,
            "agent_id": int(request.POST["agent_id"]) if request.POST.get("agent_id") else None,
            "total_runs": int(request.POST.get("total_runs", 1)),
            "max_parallel_runs": int(request.POST.get("max_parallel_runs", 1)),
            "scripts": scripts_data,
        }
        return ExperimentCreateInput.model_validate(input_data, context={"instance_names": instance_names})
    except (json.JSONDecodeError, KeyError) as exc:
        raise ExperimentValidationError(f"Invalid input: {exc}") from exc
    except ValueError as exc:
        from pydantic import ValidationError as PydanticValidationError

        if isinstance(exc, PydanticValidationError):
            field_errors = "; ".join(f"{err['loc'][-1]}: {err['msg']}" for err in exc.errors() if err.get("loc"))
            raise ExperimentValidationError(f"Validation error: {field_errors or exc}") from exc
        raise ExperimentValidationError(f"Invalid input: {exc}") from exc


def _handle_experiment_create_post(request: HttpRequest) -> HttpResponse:
    """Validate the form, create the experiment, and redirect appropriately."""
    try:
        data = _validate_experiment_create_input(request)
        experiment = services.create_experiment(cast("User", request.user), data)
    except ExperimentValidationError as e:
        messages.error(request, str(e))
        return redirect("experiments:experiment_create")
    except Exception:
        logger.exception("experiment_create: unexpected error for user_id=%s", request.user.id)
        messages.error(request, "An unexpected error occurred. Please try again.")
        return redirect("experiments:experiment_list")
    messages.success(request, f"Experiment '{experiment.name}' created.")
    return redirect("experiments:experiment_detail", experiment_id=experiment.pk)


@threat_research_required
def experiment_create(request: HttpRequest) -> HttpResponse:
    """Create a new experiment.

    GET:  Show creation form.
    POST: Validate and create experiment.
    """
    logger.info("experiment_create: user_id=%s method=%s", request.user.id, safe_log_value(request.method))
    if request.method == "GET":
        from cms.scenarios.registry import list_all_scenarios

        scenarios = list_all_scenarios(user=cast("User", request.user))
        return render(
            request,
            "experiments/experiment_create.html",
            {
                "active_nav": "experiments",
                "scenarios": scenarios,
            },
        )

    if request.method == "POST":
        return _handle_experiment_create_post(request)

    return HttpResponse(status=405)


@threat_research_required
def experiment_detail(request: HttpRequest, experiment_id: int) -> HttpResponse:
    """View experiment details and run status."""
    logger.info("experiment_detail: user_id=%s experiment_id=%s", request.user.id, safe_log_value(experiment_id))
    try:
        experiment = services.get_experiment(cast("User", request.user), experiment_id)
    except ExperimentError:
        messages.error(request, "Experiment not found.")
        return redirect("experiments:experiment_list")
    except Exception:
        logger.exception(
            "experiment_detail: unexpected error for user_id=%s",
            request.user.id,
        )
        messages.error(request, "An unexpected error occurred. Please try again.")
        return redirect("experiments:experiment_list")

    return render(
        request,
        "experiments/experiment_detail.html",
        {
            "active_nav": "experiments",
            "experiment": experiment,
        },
    )


@threat_research_required
@require_POST
def experiment_start(request: HttpRequest, experiment_id: int) -> HttpResponse:
    """Start experiment execution."""
    logger.info("experiment_start: user_id=%s experiment_id=%s", request.user.id, safe_log_value(experiment_id))
    try:
        services.start_experiment(cast("User", request.user), experiment_id)
        messages.success(request, "Experiment queued for execution.")
    except ExperimentError as e:
        # ExperimentStateError subclasses ExperimentError, so this single
        # handler covers both; a separate ExperimentStateError clause would be
        # unreachable (SonarCloud S2190 / duplicate-except).
        messages.error(request, str(e))
    except Exception:
        logger.exception(
            "experiment_start: unexpected error for user_id=%s",
            request.user.id,
        )
        messages.error(request, "An unexpected error occurred. Please try again.")
    return redirect("experiments:experiment_detail", experiment_id=experiment_id)


@threat_research_required
@require_POST
def experiment_cancel(request: HttpRequest, experiment_id: int) -> HttpResponse:
    """Cancel a running experiment."""
    logger.info("experiment_cancel: user_id=%s experiment_id=%s", request.user.id, safe_log_value(experiment_id))
    try:
        services.cancel_experiment(cast("User", request.user), experiment_id)
        messages.success(request, "Experiment cancelled.")
    except ExperimentError as e:
        # ExperimentStateError subclasses ExperimentError, so this single handler
        # covers both; listing both would be redundant (SonarCloud S5713).
        messages.error(request, str(e))
    except Exception:
        logger.exception(
            "experiment_cancel: unexpected error for user_id=%s",
            request.user.id,
        )
        messages.error(request, "An unexpected error occurred. Please try again.")
    return redirect("experiments:experiment_detail", experiment_id=experiment_id)
