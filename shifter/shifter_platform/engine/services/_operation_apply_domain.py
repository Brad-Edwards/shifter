"""Authoritative domain apply for validated operation results (ADR-043 phase 4, #1836).

``_operation_apply`` decides whether an inbox row is *admissible* (envelope,
contract version, digest, generation, ownership). This module decides whether it
is *applicable* — discriminators agree, the step is declared, the payload parses,
no conflicting sibling exists, the step legally follows what has already been
applied — and then performs the write.

Everything the applied transition implies commits together inside the caller's
``transaction.atomic()``: the domain rows, a **strict** audit row (ADR-043-R3 —
best-effort auditing is not sufficient when the audit row is the control), and
the ADR-025 ``RangeEventOutbox`` notification. A failure anywhere rolls the whole
result back and leaves the inbox row retryable rather than half-applied.

The provisioner is no longer an authoritative writer for this family: one
operation generation has exactly one authoritative path, and it is this one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from django.utils import timezone

from shared.audit import AuditEntityType, StateChange, audit_log_system_event
from shared.enums import ResourceStatus
from shared.operation_envelope import OperationEnvelopeError, validate_operation_envelope
from shared.operation_results import (
    OperationResultError,
    ResultStep,
    has_contract,
    latest_step,
    parse_result_payload,
    result_kind_for,
    step_follows,
)

if TYPE_CHECKING:
    from engine.models import Instance, OperationResultInbox, Range

logger = logging.getLogger(__name__)

_RANGE_RESOURCES = frozenset({"range", "aces-range"})
_AUDIT_SOURCE = "engine.services.operation_apply"

# Statuses on another attached range that keep a shared NGFW running. The
# provisioner's ``should_pause_ngfw`` is a pre-cloud compatibility check; this
# re-check under lock is the authorization.
_NGFW_KEEP_ALIVE_STATUSES = (ResourceStatus.READY.value, ResourceStatus.RESUMING.value)


def _discriminator_mismatch(row: OperationResultInbox, envelope: dict) -> str:
    """Return a reason when a flattened inbox column disagrees with the envelope.

    The flattened columns are what the applier queries and locks on, so a row
    whose columns disagree with the signed-shape envelope is not merely redundant
    — it is a row that would be applied under the wrong identity.
    """
    for field in ("operation_id", "request_id", "resource", "operation", "contract_version"):
        if str(getattr(row, field)) != str(envelope[field]):
            return f"inbox {field} does not match the envelope"
    return ""


def _has_conflicting_sibling(row: OperationResultInbox) -> bool:
    """Return True when another row reports this step with a different payload.

    Result identity embeds the digest, so an identical replay collapses onto one
    row at insert time and a *conflicting* replay lands as a second row for the
    same ``(operation_id, result_step)``. The provisioner cannot detect that (it
    has no inbox read grant); the applier can and must.
    """
    from engine.models import OperationResultInbox as Inbox

    digests = (
        Inbox.objects.filter(operation_id=row.operation_id, result_step=row.result_step)
        .values_list("payload_digest", flat=True)
        .distinct()
    )
    return len(set(digests)) > 1


def _applied_steps(row: OperationResultInbox) -> list[str]:
    """Return the steps already applied for this operation generation."""
    from engine.models import OperationResultDisposition
    from engine.models import OperationResultInbox as Inbox

    return list(
        Inbox.objects.filter(
            operation_id=row.operation_id,
            disposition=OperationResultDisposition.APPLIED,
        )
        .exclude(result_step="")
        .values_list("result_step", flat=True)
    )


def _has_earlier_pending_sibling(row: OperationResultInbox) -> bool:
    """Return True when an earlier-created result of this generation is still pending."""
    from engine.models import OperationResultDisposition
    from engine.models import OperationResultInbox as Inbox

    return (
        Inbox.objects.filter(
            operation_id=row.operation_id,
            disposition=OperationResultDisposition.PENDING,
            created_at__lt=row.created_at,
        )
        .exclude(pk=row.pk)
        .exists()
    )


def _lock_range(operation_id) -> Range | None:
    """Lock and return the Range that currently owns this operation generation.

    No ``select_related`` here on purpose: ``Range.ngfw_instance`` is nullable, and
    PostgreSQL refuses ``SELECT ... FOR UPDATE`` over the nullable side of an outer
    join. Related rows are resolved afterwards and locked explicitly, in the
    declared primary-key order, by the paths that mutate them.
    """
    from engine.models import Range

    return Range.objects.select_for_update().filter(provisioner_operation_id=operation_id).first()


def _lock_ngfw_instance(operation_id) -> Instance | None:
    """Lock and return the NGFW Instance that currently owns this generation."""
    from engine.models import Instance

    return (
        Instance.objects.select_for_update()
        .filter(provisioner_operation_id=operation_id, role=Instance.Role.NGFW)
        .first()
    )


def _lock_instance_by_pk(pk: int) -> Instance | None:
    """Lock one Instance row explicitly (used after a nullable FK is resolved)."""
    from engine.models import Instance

    return Instance.objects.select_for_update().filter(pk=pk).first()


def _audit(
    entity_type: str,
    entity_id: int,
    new: str,
    *,
    request_id: str,
    previous: dict | None = None,
    detail: dict | None = None,
    context: str = "",
) -> None:
    """Write the transition's audit row strictly, inside the caller's transaction.

    ``entity_id`` is 0 for UUID-identified entities (NGFW): ``AuditLog.entity_id``
    is a PositiveIntegerField, so passing a UUID there raises and loses the row.
    The UUIDs go in the state instead — the same convention ``engine.handlers``
    already uses.
    """
    from engine.handlers._audit import _status_to_action

    audit_log_system_event(
        entity_type=entity_type,
        entity_id=entity_id,
        action=_status_to_action(new),
        source=_AUDIT_SOURCE,
        state=StateChange(previous=previous or {}, new={"status": new, **(detail or {})}),
        context=context,
        request_id=request_id,
        strict=True,
    )


def _enqueue_range_status_event(range_obj: Range, new_status: str, error_message: str) -> None:
    """Enqueue the ADR-025 range status notification for an applied transition."""
    from engine.models import RangeEventOutbox
    from shared.messages.events import EVENT_TYPE_STATUS_UPDATED

    event_id = uuid4()
    related_request = range_obj.request
    RangeEventOutbox.objects.create(
        event_id=event_id,
        event_type=EVENT_TYPE_STATUS_UPDATED,
        payload={
            "event_type": EVENT_TYPE_STATUS_UPDATED,
            "event_id": str(event_id),
            "timestamp": timezone.now().isoformat(),
            "request_id": str(related_request.request_id) if related_request is not None else "",
            "range_id": range_obj.id,
            "user_id": range_obj.user_id,
            "new_status": new_status,
            "error_message": error_message,
        },
        next_attempt_at=timezone.now(),
    )


def _enqueue_ngfw_status_event(ngfw: Instance, app, new_status: str, request_id: str) -> None:
    """Enqueue the NGFW lifecycle notification for an applied transition.

    The provisioner used to write this row itself (``events.publish_ngfw_event``).
    After cutover the notification belongs to the applier, committed in the same
    transaction as the state it describes, so a consumer can never observe an
    event for a transition that rolled back. Same notification-only shape as
    before: identifiers and status, no state.
    """
    from engine.models import RangeEventOutbox
    from shared.messages.events import EVENT_TYPE_NGFW

    event_id = uuid4()
    RangeEventOutbox.objects.create(
        event_id=event_id,
        event_type=EVENT_TYPE_NGFW,
        payload={
            "event_type": EVENT_TYPE_NGFW,
            "event_id": str(event_id),
            "timestamp": timezone.now().isoformat(),
            "request_id": request_id,
            "instance_id": str(ngfw.uuid),
            "app_id": str(app.uuid) if app is not None else None,
            "status": new_status,
        },
        next_attempt_at=timezone.now(),
    )


def _save_status(obj, new_status: str, extra_fields: dict | None = None) -> str:
    """Set status (+ timestamps) on a locked row and return the previous status."""
    previous = obj.status
    now = timezone.now()
    obj.status = new_status
    obj.updated_at = now
    update_fields = ["status", "updated_at"]
    for field, value in (extra_fields or {}).items():
        setattr(obj, field, value)
        update_fields.append(field)
    obj.save(update_fields=update_fields)
    return previous


def _apply_instances(range_obj: Range, payload: dict, request_id: str) -> str:
    """Apply a bounded set of instance outcomes belonging to this Range's request.

    Locked in primary-key order, and matched against the closed UUID set the
    result carries — never a blanket update of every instance for the request.
    """
    from engine.models import Instance

    wanted = {outcome["instance_uuid"]: outcome["status"] for outcome in payload["instances"]}
    # The permitted set is derived server-side from the locked Range's request,
    # and NGFW-role instances are excluded outright: a shared NGFW may only move
    # through the cascade path, which checks attachment and whether another
    # attached Range still needs it. Without this exclusion an inbox INSERT --
    # the provisioner principal's one remaining capability -- could name the
    # attached NGFW in an ordinary instance result and bypass both checks.
    rows = list(
        Instance.objects.select_for_update()
        .filter(request=range_obj.request, uuid__in=list(wanted))
        .exclude(role=Instance.Role.NGFW)
        .order_by("pk")
    )
    found = {str(row.uuid) for row in rows}
    missing = sorted(set(wanted) - found)
    if missing:
        raise _NotApplicable(f"result names {len(missing)} instance(s) outside the operation's lifecycle target set")

    applied = []
    for row in rows:
        new_status = wanted[str(row.uuid)]
        previous = _save_status(row, new_status)
        applied.append({"instance_uuid": str(row.uuid), "previous": previous})

    # One audit row for the transition, against the Range that owns it: the
    # instances share a status and an operation generation, and their identity is
    # UUID-based (not a valid AuditLog.entity_id).
    _audit(
        AuditEntityType.RANGE,
        range_obj.id,
        payload["instances"][0]["status"],
        request_id=request_id,
        previous={"instances": applied},
        detail={"instance_count": len(rows)},
    )
    return f"{len(rows)} instance(s)"


def _resolve_cascade_ngfw(range_obj: Range, payload: dict) -> Instance:
    """Resolve the NGFW a Range operation may cascade to, or refuse.

    Ownership runs through ``Range.ngfw_instance`` — never through a
    payload-supplied id — so one Range generation cannot mutate an arbitrary
    NGFW.
    """
    if range_obj.ngfw_instance_id is None:
        raise _NotApplicable("range has no attached NGFW to cascade to")
    # Resolve through the FK, then take the row lock explicitly: the owning Range
    # is already locked, so this is the second lock in the declared order.
    ngfw = _lock_instance_by_pk(range_obj.ngfw_instance_id)
    if ngfw is None:
        raise _NotApplicable("attached NGFW no longer exists")
    if str(ngfw.uuid) != payload["ngfw_instance_uuid"]:
        raise _NotApplicable("result NGFW does not match the range's attached NGFW")
    return ngfw


def _other_attached_range_needs_ngfw(range_obj: Range, ngfw: Instance) -> bool:
    """Return True when another attached Range still needs this shared NGFW."""
    from engine.models import Range

    return (
        Range.objects.filter(ngfw_instance=ngfw, status__in=_NGFW_KEEP_ALIVE_STATUSES).exclude(pk=range_obj.pk).exists()
    )


def _apply_ngfw(ngfw: Instance, new_status: str, request_id: str) -> None:
    """Apply an NGFW Instance transition and its NGFW App projection."""
    from engine.models import App

    previous = _save_status(ngfw, new_status, _terminal_timestamps(new_status))

    # Only the NGFW App projection belongs to this lifecycle, never every App
    # beneath the instance.
    apps = list(App.objects.select_for_update().filter(instance=ngfw, app_type=App.AppType.NGFW).order_by("pk"))
    for app in apps:
        _save_status(app, new_status, _terminal_timestamps(new_status))

    _audit(
        AuditEntityType.NGFW,
        0,
        new_status,
        request_id=request_id,
        previous={"status": previous},
        detail={"instance_uuid": str(ngfw.uuid), "app_uuids": [str(app.uuid) for app in apps]},
    )
    _enqueue_ngfw_status_event(ngfw, apps[0] if apps else None, new_status, request_id)


def _terminal_timestamps(new_status: str) -> dict:
    """Return the timestamp columns a terminal status also sets."""
    if new_status == ResourceStatus.DESTROYED.value:
        return {"destroyed_at": timezone.now()}
    return {}


class _NotApplicable(Exception):
    """A validated result that must not mutate this domain state."""


def _apply_range_terminal(range_obj: Range, payload: dict, request_id: str) -> str:
    """Apply the terminal status of a Range pause/resume."""
    new_status = payload["status"]
    extra = {}
    if new_status == ResourceStatus.READY.value:
        extra["ready_at"] = timezone.now()
    if new_status == ResourceStatus.PAUSED.value:
        extra["paused_at"] = timezone.now()
    previous = _save_status(range_obj, new_status, extra)
    _audit(AuditEntityType.RANGE, range_obj.id, new_status, request_id=request_id, previous={"status": previous})
    _enqueue_range_status_event(range_obj, new_status, "")
    return f"range -> {new_status}"


def _apply_failure(target, payload: dict, request_id: str, is_range: bool) -> str:
    """Apply a terminal failure, carrying only the authored reason code."""
    from engine.launch_intents import clear_provisioner_operation_after_failure

    new_status = ResourceStatus.FAILED.value
    context = payload["reason_code"]
    previous = target.status
    now = timezone.now()
    target.status = new_status
    target.updated_at = now
    target.error_message = context
    update_fields = ["status", "updated_at", "error_message"]
    update_fields.extend(clear_provisioner_operation_after_failure(target))
    target.save(update_fields=update_fields)

    if is_range:
        _audit(
            AuditEntityType.RANGE,
            target.id,
            new_status,
            request_id=request_id,
            previous={"status": previous},
            context=context,
        )
    else:
        _audit(
            AuditEntityType.NGFW,
            0,
            new_status,
            request_id=request_id,
            previous={"status": previous},
            detail={"instance_uuid": str(target.uuid)},
            context=context,
        )
    if is_range:
        _enqueue_range_status_event(target, new_status, context)
    return f"{'range' if is_range else 'ngfw'} -> failed ({context})"


def _lock_operation_target(row: OperationResultInbox):
    """Lock the domain row that owns this operation generation, or return None.

    Locking happens BEFORE any conflict or ordering decision. All results for one
    generation resolve to the same row, so this lock is the serialization
    boundary: two appliers claiming sibling results with ``skip_locked`` cannot
    evaluate history concurrently and both decide they may apply.
    """
    if row.resource in _RANGE_RESOURCES:
        return _lock_range(row.operation_id)
    return _lock_ngfw_instance(row.operation_id)


def _dispatch(row: OperationResultInbox, step: ResultStep, payload: dict, target) -> str:
    """Route one validated result to its domain write. Caller holds the lock."""
    is_range_resource = row.resource in _RANGE_RESOURCES
    request_id = str(row.request_id)

    if is_range_resource:
        range_obj = target
        if step in (ResultStep.RANGE_INSTANCES_PAUSED, ResultStep.RANGE_INSTANCES_READY):
            return _apply_instances(range_obj, payload, request_id)
        if step is ResultStep.RANGE_TERMINAL_FAILED:
            return _apply_failure(range_obj, payload, request_id, is_range=True)
        if step in (ResultStep.RANGE_TERMINAL_PAUSED, ResultStep.RANGE_TERMINAL_READY):
            return _apply_range_terminal(range_obj, payload, request_id)
        # NGFW cascade steps are subordinate results of the Range generation.
        ngfw = _resolve_cascade_ngfw(range_obj, payload)
        new_status = payload["status"]
        if new_status in (ResourceStatus.PAUSING.value, ResourceStatus.PAUSED.value) and (
            _other_attached_range_needs_ngfw(range_obj, ngfw)
        ):
            raise _NotApplicable("another attached range still needs this NGFW")
        _apply_ngfw(ngfw, new_status, request_id)
        return f"ngfw cascade -> {new_status}"

    ngfw = target
    if step is ResultStep.NGFW_TERMINAL_FAILED:
        return _apply_failure(ngfw, payload, request_id, is_range=False)
    if str(ngfw.uuid) != payload["ngfw_instance_uuid"]:
        raise _NotApplicable("result NGFW does not match the operation target")
    _apply_ngfw(ngfw, payload["status"], request_id)
    return f"ngfw -> {payload['status']}"


def apply_validated_result(row: OperationResultInbox) -> tuple[str, str]:
    """Apply one admissible inbox row to domain state. Returns ``(disposition, detail)``.

    The caller supplies the transaction; every write this makes is rolled back
    with it. Deterministic refusals (invalid, conflicting, out-of-order, not
    applicable) return a disposition and mutate nothing. Transient failures
    propagate so the row stays retryable.
    """
    from engine.models import OperationResultDisposition

    try:
        envelope = validate_operation_envelope(row.envelope)
    except OperationEnvelopeError as exc:
        return OperationResultDisposition.REJECTED_INVALID, str(exc)[:128]

    mismatch = _discriminator_mismatch(row, envelope)
    if mismatch:
        return OperationResultDisposition.REJECTED_INVALID, mismatch

    # Families not yet cut over stay in shadow: they have no authoritative
    # contract here and direct provisioner SQL is still their sole writer, so
    # applying (or rejecting) them would be wrong. Phase 4 migrates the
    # pause/resume + NGFW family only.
    if not has_contract(row.resource, row.operation):
        return OperationResultDisposition.VALIDATED, "shadow: family not yet cut over"
    if not row.result_step:
        return OperationResultDisposition.VALIDATED, "shadow: result predates the step contract"

    try:
        step = ResultStep(row.result_step)
        if result_kind_for(row.resource, row.operation, step=step) != row.result_kind:
            return OperationResultDisposition.REJECTED_INVALID, "result_kind does not match the declared step"
        payload = parse_result_payload(row.resource, row.operation, step=step, payload=envelope["payload"])
    except ValueError:
        return OperationResultDisposition.REJECTED_INVALID, f"unknown result step '{row.result_step}'"[:128]
    except OperationResultError as exc:
        return OperationResultDisposition.REJECTED_INVALID, str(exc)[:128]

    # Take the generation's row lock BEFORE deciding conflict or ordering. Every
    # result for one operation resolves to the same target, so this serializes
    # sibling results: without it two appliers can each read an empty applied
    # history and both decide they may apply.
    target = _lock_operation_target(row)
    if target is None:
        return OperationResultDisposition.REJECTED_STALE, "operation generation is no longer current"

    if _has_conflicting_sibling(row):
        return (
            OperationResultDisposition.REJECTED_CONFLICT,
            "another result for this step carries a different payload",
        )

    # Do not advance past a still-pending earlier sibling. Claiming order is
    # created_at, but skip_locked lets a worker reach a later result first;
    # applying it would make the earlier step arrive "late" and be rejected.
    # Leaving this row PENDING lets the next pass take them in order.
    if _has_earlier_pending_sibling(row):
        return "", ""

    previous_step = latest_step(row.resource, row.operation, _applied_steps(row))
    if not step_follows(row.resource, row.operation, previous=previous_step, step=step):
        return (
            OperationResultDisposition.REJECTED_ORDERING,
            f"step may not follow '{previous_step}'"[:128],
        )

    try:
        detail = _dispatch(row, step, payload, target)
    except _NotApplicable as exc:
        return OperationResultDisposition.REJECTED_OWNERSHIP, str(exc)[:128]
    return OperationResultDisposition.APPLIED, detail[:128]
