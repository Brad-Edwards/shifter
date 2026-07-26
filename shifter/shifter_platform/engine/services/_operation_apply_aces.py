"""Authoritative apply for ACES operation results (ADR-043 phase 5, #1837).

``_operation_apply_domain`` routes here once a result has been admitted
(envelope, discriminators, contract version, digest, generation, ownership,
conflict, ordering). This module owns what an applied ACES observation implies:
the sidecar evidence record, and -- for the steps that carry a lifecycle
projection -- the Range status transition, its strict audit row, and the ADR-025
notification.

Everything commits inside the caller's transaction, so a sidecar validation
failure, an audit failure, or an outbox failure rolls the whole result back and
leaves the inbox row retryable.

Two things this deliberately does NOT do:

* It does not call ``engine.services.project_aces_operation_status`` or
  ``record_aces_operation_status``. Those are the pre-cutover event-consumer
  path: the first enqueues a second outbox workflow, and both separate the
  sidecar write from the result disposition. Their persisters in
  ``shared.aces.operations`` are reused directly instead, so evidence and
  disposition share one transaction.
* It does not derive any timestamp from the wall clock. The sidecar's
  idempotency key is ``<kind>:<operation_id>:<source_timestamp>``, so a
  re-applied row must reproduce the same instant or a replay would fork into a
  second record. The inbox row's ``created_at`` is that stable instant.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from django.utils import timezone

from shared.aces.status import ACES_STATE_FAILED
from shared.audit import AuditEntityType
from shared.enums import ResourceStatus
from shared.operation_results import ResultStep, range_status_for

from ._operation_apply_effects import _audit, _enqueue_range_status_event, _save_status, _terminal_timestamps

if TYPE_CHECKING:
    from engine.models import OperationResultInbox, Range

logger = logging.getLogger(__name__)

# Steps whose evidence is a runtime snapshot rather than an operation status.
_SNAPSHOT_STEPS = frozenset({ResultStep.ACES_PROVISION_SNAPSHOT})

#: The shared terminal-failure writer owned by ``_operation_apply_domain``.
#: Passed in rather than imported so this module does not depend back on its
#: dispatcher: ``(target, payload, request_id, *, is_range) -> detail``.
ApplyFailure = Callable[..., str]


def _sidecar_payload(row: OperationResultInbox, **extra: Any) -> dict[str, Any]:
    """Build the common sidecar payload keys for one result.

    Mirrors the shape the pre-cutover ``range.aces.operation`` /
    ``range.aces.snapshot`` consumers produced, so historical and new evidence
    rows stay readable through the same Mission Control projections.
    """
    return {
        "operation_id": str(row.operation_id),
        "request_id": str(row.request_id),
        **extra,
    }


def _persist_operation_status(row: OperationResultInbox, state: str, status_reason: str | None) -> None:
    """Persist one ACES ``operation_status`` sidecar record for this result."""
    # Lazy import: shared.aces.operations pulls shared.models, which must not
    # load during Django app population (AppRegistryNotReady) -- the same
    # constraint _aces_evidence and _aces_range already observe.
    from shared.aces.operations import persist_operation_status_record

    source_timestamp = row.created_at
    payload = _sidecar_payload(row, status=state, source_timestamp=source_timestamp.isoformat())
    if status_reason:
        payload["status_reason"] = status_reason
    persist_operation_status_record(
        request_id=row.request_id,
        operation_id=str(row.operation_id),
        source_timestamp=source_timestamp,
        payload=payload,
    )


def _persist_runtime_snapshot(row: OperationResultInbox, resources: list[dict[str, Any]]) -> None:
    """Persist one ACES ``runtime_snapshot`` sidecar record for this result."""
    from shared.aces.operations import persist_runtime_snapshot_record

    source_timestamp = row.created_at
    persist_runtime_snapshot_record(
        request_id=row.request_id,
        operation_id=str(row.operation_id),
        source_timestamp=source_timestamp,
        payload=_sidecar_payload(row, resources=resources, captured_at=source_timestamp.isoformat()),
    )


def _apply_lifecycle(range_obj: Range, new_status: str, request_id: str) -> str:
    """Apply an ACES range transition with its audit row and notification."""
    extra = _terminal_timestamps(new_status)
    if new_status == ResourceStatus.READY.value:
        extra = {**extra, "ready_at": timezone.now()}
    previous = _save_status(range_obj, new_status, extra)
    _audit(AuditEntityType.RANGE, range_obj.id, new_status, request_id=request_id, previous={"status": previous})
    _enqueue_range_status_event(range_obj, new_status, "")
    return f"aces range -> {new_status}"


def _apply_observation(row: OperationResultInbox, step: ResultStep, payload: dict[str, Any], range_obj: Range) -> str:
    """Record an ACES observation and apply the range status it projects, if any."""
    _persist_operation_status(row, payload["aces_status"], payload.get("status_reason"))
    new_status = range_status_for(row.resource, row.operation, step=step)
    if new_status is None:
        return f"aces {payload['aces_status']} (evidence only)"
    return _apply_lifecycle(range_obj, new_status, str(row.request_id))


def apply_aces_result(
    row: OperationResultInbox,
    step: ResultStep,
    payload: dict[str, Any],
    range_obj: Range,
    apply_failure: ApplyFailure,
) -> str:
    """Apply one admitted ACES provision/destroy result. Caller holds the lock.

    ``apply_failure`` is the shared terminal-failure writer from
    ``_operation_apply_domain``; failure handling is identical across families
    (authored reason code onto the row, generation cleared, audit, notification)
    and is not reimplemented here.
    """
    if step in _SNAPSHOT_STEPS:
        # Bounded evidence only: no status write, no audit row, no range event.
        _persist_runtime_snapshot(row, payload["resources"])
        return f"aces snapshot ({len(payload['resources'])} resource(s))"

    if step is ResultStep.ACES_TERMINAL_FAILED:
        # The sidecar still records the failed observation; only the closed
        # reason code travels as its reason, never the bounded diagnostic.
        _persist_operation_status(row, ACES_STATE_FAILED, payload["reason_code"])
        return apply_failure(range_obj, payload, str(row.request_id), is_range=True)

    return _apply_observation(row, step, payload, range_obj)
