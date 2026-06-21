"""Shared CMS range pause/resume lifecycle helper.

``pause_range`` and ``resume_range`` (each with a ``_by_request_id`` variant)
were near-identical copies that differed only in operation-specific facts. This
module parameterizes those facts on an ``_LifecycleOp`` spec so the validation,
ownership masking, CMS status transition + revert-on-engine-rejection, and audit
logging live in one place. It mirrors the engine-side parameterized pattern in
``engine.services._lifecycle``.

The thin ``_range_pause`` / ``_range_resume`` modules keep the public function
names (so ``cms.services`` re-exports are unchanged) and delegate here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cms.exceptions import CMSError
from cms.models import RangeInstance
from risk_register.models import AuditLog
from shared.constants import USER_CANNOT_BE_NONE
from shared.enums import ResourceStatus

from ._common import _validate_caller_user

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


def _engine_pause_range_call(request_id: Any) -> Any:  # NOSONAR
    """Late-bound call so test patches of cms.services.engine_pause_range apply."""
    from cms import services as _cs

    return _cs.engine_pause_range(request_id)


def _engine_resume_range_call(request_id: Any) -> Any:  # NOSONAR
    """Late-bound call so test patches of cms.services.engine_resume_range apply."""
    from cms import services as _cs

    return _cs.engine_resume_range(request_id)


def _audit_log_call(**kwargs: Any) -> None:  # NOSONAR
    """Late-bound call to ``cms.services.audit_log`` so test patches apply."""
    from cms import services as _cs

    _cs.audit_log(_cs.AuditEvent(**kwargs))


@dataclass(frozen=True)
class _LifecycleOp:
    """Operation-specific facts that distinguish pause from resume."""

    name: str  # log-message prefix, e.g. "pause_range"
    verb: str  # short verb for the "cannot <verb>" log, e.g. "pause"
    engine_call: Callable[[Any], Any]
    target_status: str
    revert_status: str
    audit_action: Any
    failure_message: str


PAUSE_OP = _LifecycleOp(
    name="pause_range",
    verb="pause",
    engine_call=_engine_pause_range_call,
    target_status=ResourceStatus.PAUSING.value,
    revert_status=ResourceStatus.READY.value,
    audit_action=AuditLog.Action.PAUSE,
    failure_message="Range cannot be paused in current state",
)

RESUME_OP = _LifecycleOp(
    name="resume_range",
    verb="resume",
    engine_call=_engine_resume_range_call,
    target_status=ResourceStatus.RESUMING.value,
    revert_status=ResourceStatus.PAUSED.value,
    audit_action=AuditLog.Action.RESUME,
    failure_message="Range cannot be resumed in current state",
)


def _attempt_transition(
    instance: RangeInstance,
    op: _LifecycleOp,
    *,
    request_id: Any,
    audit_entity_id: int,
    user: User,
    label: str,
    engine_false_detail: str,
) -> None:
    """Set CMS status, dispatch to the engine, revert on rejection, then audit."""
    instance.status = op.target_status
    instance.save(update_fields=["status"])

    if not op.engine_call(request_id):
        instance.status = op.revert_status
        instance.save(update_fields=["status"])
        logger.warning("%s: engine returned False for %s", label, engine_false_detail)
        raise CMSError(op.failure_message)

    _audit_log_call(
        entity_type=AuditLog.EntityType.RANGE,
        entity_id=audit_entity_id,
        action=op.audit_action,
        actor_type=AuditLog.ActorType.USER,
        actor_id=user.id,
        new_state={"status": op.target_status},
        request_id=str(request_id),
    )


def run_by_range_id(user: User, range_id: int, op: _LifecycleOp) -> None:
    """Run a pause/resume operation against a range identified by ``range_id``.

    Fetches the RangeInstance, verifies ownership, updates CMS status, then
    delegates to the engine facade. Status is reverted and a ``CMSError`` raised
    when the engine rejects the transition.
    """
    label = op.name
    _validate_caller_user(user, label)

    if range_id is None:
        logger.error("%s called with None range_id for user_id=%s", label, user.id)
        raise TypeError("range_id cannot be None")

    if not isinstance(range_id, int):
        logger.error("%s called with invalid range_id type: %s", label, type(range_id).__name__)
        msg = f"range_id must be an int, got {type(range_id).__name__}"
        raise TypeError(msg)

    if range_id < 0:
        logger.error("%s called with negative range_id=%s for user_id=%s", label, range_id, user.id)
        raise ValueError("range_id must be non-negative")

    logger.debug("%s called for user_id=%s, range_id=%s", label, user.id, range_id)

    try:
        instance = RangeInstance.objects.get(range_id=range_id)
    except RangeInstance.DoesNotExist:
        logger.warning("%s: range not found for user_id=%s, range_id=%s", label, user.id, range_id)
        raise CMSError(f"Range {range_id} not found") from None

    if instance.user_id != user.id:
        logger.error(
            "%s: access denied - range_id=%s owned by user_id=%s, requested by user_id=%s",
            label,
            range_id,
            instance.user_id,
            user.id,
        )
        raise CMSError(f"Range {range_id} not found")

    try:
        request_id = instance.request.request_id if instance.request else None
        if request_id is None:
            logger.error("%s: no request_id for range_id=%s, cannot %s", label, range_id, op.verb)
            raise CMSError("Range has no associated request")

        _attempt_transition(
            instance,
            op,
            request_id=request_id,
            audit_entity_id=range_id,
            user=user,
            label=label,
            engine_false_detail=f"range_id={range_id}",
        )

        logger.info("%s completed: range_id=%s user_id=%s", label, range_id, user.id)
    except (TypeError, ValueError, CMSError):
        raise
    except Exception:
        logger.exception("Error in %s: user_id=%s range_id=%s", label, user.id, range_id)
        raise


def run_by_request_id(user: User, request_id: str, op: _LifecycleOp) -> None:
    """Run a pause/resume operation against a range identified by ``request_id``.

    Fetches the owned RangeInstance by request_id, then delegates to the engine
    facade. Status is reverted and a ``CMSError`` raised when the engine rejects
    the transition.
    """
    label = f"{op.name}_by_request_id"

    if user is None:
        logger.error("%s called with None user", label)
        raise TypeError(USER_CANNOT_BE_NONE)

    if not hasattr(user, "id"):
        logger.error("%s called with invalid user type: %s", label, type(user).__name__)
        msg = f"user must be a User instance, got {type(user).__name__}"
        raise TypeError(msg)

    if not request_id:
        logger.error("%s called with empty request_id", label)
        raise CMSError("request_id is required")

    logger.debug("%s called: user_id=%s request_id=%s", label, user.id, request_id)

    instance = RangeInstance.objects.filter(
        request__request_id=request_id,
        user_id=user.id,
    ).first()

    if not instance:
        logger.warning("%s: not found: request_id=%s user_id=%s", label, request_id, user.id)
        raise CMSError("Range not found")

    if instance.request is None:
        raise CMSError("Range has no associated request")

    try:
        _attempt_transition(
            instance,
            op,
            request_id=instance.request.request_id,
            audit_entity_id=instance.range_id or 0,
            user=user,
            label=label,
            engine_false_detail=f"request_id={request_id}",
        )

        logger.info("%s completed: request_id=%s user_id=%s", label, request_id, user.id)
    except (TypeError, ValueError, CMSError):
        raise
    except Exception:
        logger.exception("Error in %s: user_id=%s request_id=%s", label, user.id, request_id)
        raise
