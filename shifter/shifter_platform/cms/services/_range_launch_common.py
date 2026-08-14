"""Common admission and persistence invariants for RAES range launches."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.db import IntegrityError, transaction

from cms.exceptions import CMSError
from cms.models import ACTIVE_RANGE_UNIQUE_CONSTRAINT, RangeInstance
from cms.services._range_workspace import (
    reauthorize_launch_workspace_locked,
    resolve_effective_egress_mode_locked,
)
from shared.constants import USER_CANNOT_BE_NONE, USER_MUST_BE_SAVED
from shared.enums import ResourceStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.contrib.auth.models import User

    from cms.models import Request
    from shared.enums import RangeSource

logger = logging.getLogger(__name__)

_ACTIVE_RANGE_MESSAGE = "You already have an active range. Please destroy it before creating a new one."


@dataclass(frozen=True, slots=True)
class LaunchOptions:
    """Optional inputs retained at the public range-launch seam."""

    ngfw_enabled: bool = False
    remote_access_teardown_at: datetime | None = None
    workspace_uuid: str | UUID | None = None


def _audit_log_call(**kwargs: Any) -> None:  # NOSONAR
    """Late-bind the public audit seam so established test patches apply."""
    from cms import services as cms_services

    cms_services.audit_log(cms_services.AuditEvent(**kwargs))


def _validate_create_range_user(user: User) -> None:
    """Validate the launcher's public argument shape."""
    if user is None:
        logger.error("create_range called with None user")
        raise TypeError(USER_CANNOT_BE_NONE)
    if not hasattr(user, "id"):
        logger.error("create_range called with invalid user type: %s", type(user).__name__)
        raise TypeError(f"user must be a User instance, got {type(user).__name__}")
    if user.id is None:
        logger.error("create_range called with unsaved user (id=None)")
        raise ValueError(USER_MUST_BE_SAVED)


def _validate_create_range_scenario(user: User, scenario: str) -> None:
    """Validate the registered scenario identifier argument."""
    if scenario is None:
        logger.error("create_range called with None scenario for user_id=%s", user.id)
        raise ValueError("scenario cannot be None")
    if not isinstance(scenario, str) or not scenario:
        logger.error("create_range called with invalid scenario '%s' for user_id=%s", scenario, user.id)
        raise ValueError("scenario must be a non-empty string")


def _assert_scenario_launchable(scenario: str) -> None:
    """Reject a registered RAES source that has not passed launch admission."""
    from cms.scenarios.registry import get_catalog_entry

    entry = get_catalog_entry(scenario)
    if entry is not None and not entry.get("launchable", True):
        logger.warning("create_range: scenario '%s' is not launchable", scenario)
        raise CMSError(f"Scenario '{scenario}' is not available for launch")


def _get_active_range_call(user: User, range_source: RangeSource | None = None) -> Any:  # NOSONAR
    """Late-bind active-range lookup so established test patches apply."""
    from cms import services as cms_services

    return cms_services.get_active_range(user, range_source)


def _assert_no_active_range(user: User, range_source: RangeSource | None = None) -> None:
    """Fail fast when the user already occupies the source's active slot."""
    existing = _get_active_range_call(user, range_source)
    if existing:
        logger.warning(
            "create_range: user_id=%s already has active %s range request_id=%s",
            user.id,
            range_source,
            existing.range_id,
        )
        raise CMSError(_ACTIVE_RANGE_MESSAGE)


def _is_active_range_conflict(exc: IntegrityError) -> bool:
    """Return whether an integrity error is the active-range constraint."""
    cause = exc.__cause__
    diag = getattr(cause, "diag", None)
    if getattr(diag, "constraint_name", None) == ACTIVE_RANGE_UNIQUE_CONSTRAINT:
        return True
    message = str(exc)
    if ACTIVE_RANGE_UNIQUE_CONSTRAINT in message:
        return True
    return "user_id" in message and "range_source" in message


def _create_cms_request(user: User, workspace_id: int, request_id: UUID) -> Request:
    """Create the CMS request row for a pre-minted correlation identifier."""
    from cms.models import Request
    from shared.enums import RequestType

    request = Request.objects.create(
        request_id=request_id,
        request_type=RequestType.RANGE.value,
        user=user,
        workspace_id=workspace_id,
    )
    logger.info("create_range: created CMS Request id=%s for user_id=%s", request_id, user.id)
    return request


def _set_range_instance_status(range_instance: RangeInstance, status: ResourceStatus) -> None:
    """Persist the public status vocabulary on a CMS range instance."""
    range_instance.status = status.value
    range_instance.save(update_fields=["status"])


def _reserve_active_range_slot(
    user: User,
    range_source: RangeSource,
    persist_instance: Callable[[Request], RangeInstance],
    workspace_id: int,
    request_id: UUID | None = None,
) -> tuple[UUID, Request, RangeInstance, str]:
    """Atomically reauthorize scope and reserve the user's active-range slot."""
    from uuid import uuid4

    correlation_id = request_id or uuid4()
    try:
        with transaction.atomic():
            reauthorize_launch_workspace_locked(user, workspace_id)
            egress_mode = resolve_effective_egress_mode_locked(workspace_id)
            cms_request = _create_cms_request(user, workspace_id, correlation_id)
            range_instance = persist_instance(cms_request)
            _set_range_instance_status(range_instance, ResourceStatus.PROVISIONING)
    except IntegrityError as exc:
        if _is_active_range_conflict(exc):
            logger.warning(
                "create_range: active-range constraint collision for user_id=%s range_source=%s",
                user.id,
                range_source.value,
            )
            raise CMSError(_ACTIVE_RANGE_MESSAGE) from exc
        raise
    return correlation_id, cms_request, range_instance, egress_mode
