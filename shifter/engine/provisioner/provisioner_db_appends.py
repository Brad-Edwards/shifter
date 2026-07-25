"""Provisioner_db append helpers for the async-processing tables.

Split out of ``provisioner_db.py`` (Sonar S104), following the same convention as
``provisioner_db_ngfw.py`` / ``provisioner_db_aces.py``. Owns the transactional,
optional-cursor append helpers for the two async-processing tables the provisioner
writes to:

* ``engine_range_event_outbox`` (#476) via :func:`enqueue_event_outbox`;
* ``engine_operation_result_inbox`` (ADR-043 Phase 2, #1834) via
  :func:`append_operation_result`.

Both ride the caller's cursor when one is supplied (atomic with the authoritative
state write) or open a dedicated connection and commit otherwise. The operation
result append is *shadow mode*: the direct SQL writes performed by
``provisioner_db.write_provisioned_state`` /
``provisioner_db.mark_range_instances_destroyed`` /
``ngfw_runtime.update_instance_state`` remain the sole authoritative writers, so
every append here is best-effort and must never fail an authoritative operation.

``OperationRef`` is the parameter object that carries the operation identity
(request + canonical generation) so leaf writers thread one value instead of a
pair of loose primitives.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import psycopg
from cyberscript.enums import ResourceStatus
from shared.operation_envelope import build_operation_envelope, canonical_payload_digest

from log_redact import safe_log_fingerprint

logger = logging.getLogger(__name__)

_OPERATION_RESULT_APPEND_FAILED = "operation_result_inbox_append_failed"
_OPERATION_RESULT_CONFLICT = "operation_result_inbox_conflict"


@dataclass(frozen=True)
class OperationRef:
    """Identity of one provisioner operation generation (ADR-043, #1834).

    ``operation_id`` is ``None`` on local-dev runs / commands not yet carrying a
    canonical generation; the shadow append is skipped entirely in that case.
    """

    request_id: str
    operation_id: str | None = None


_EVENT_OUTBOX_INSERT_SQL = """
    INSERT INTO engine_range_event_outbox
        (event_id, event_type, payload, status, attempts, max_attempts,
         next_attempt_at, created_at)
    VALUES
        (%s, %s, %s, 'PENDING', 0, 10, NOW(), NOW())
    ON CONFLICT (event_id) DO NOTHING
"""


def enqueue_event_outbox(event: dict[str, object], *, cur: psycopg.Cursor[tuple[object, ...]] | None = None) -> None:
    """Insert an event into the transactional outbox for durable delivery.

    When ``cur`` is provided the INSERT is executed on that cursor and the caller
    owns the surrounding transaction/commit (atomic with the state change). When
    ``cur`` is None a new connection is opened, the row is inserted, and the
    connection is committed immediately. ``ON CONFLICT (event_id) DO NOTHING``
    makes the call idempotent.

    Args:
        event: Full event dict; must contain ``event_id`` and ``event_type``.
        cur:   Optional psycopg cursor sharing the caller's transaction.

    Raises:
        Exception: Any DB error is re-raised -- callers must learn when durable
            recording fails.
    """
    params = (str(event["event_id"]), event["event_type"], json.dumps(event))

    if cur is not None:
        cur.execute(_EVENT_OUTBOX_INSERT_SQL, params)
    else:
        from provisioner_db import get_db_connection

        with get_db_connection() as conn:
            with conn.cursor() as _cur:
                _cur.execute(_EVENT_OUTBOX_INSERT_SQL, params)
            conn.commit()


_APPEND_OPERATION_RESULT_INSERT_SQL = """
    INSERT INTO engine_operation_result_inbox
        (operation_id, request_id, resource, operation, contract_version,
         result_kind, result_identity, payload_digest, envelope,
         disposition, disposition_detail, created_at)
    VALUES
        (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'PENDING', '', NOW())
    ON CONFLICT (result_identity) DO NOTHING
"""

_APPEND_OPERATION_RESULT_SELECT_DIGEST_SQL = """
    SELECT payload_digest FROM engine_operation_result_inbox WHERE result_identity = %s
"""


def _insert_operation_result(
    cur: psycopg.Cursor[tuple[object, ...]],
    *,
    result_identity: str,
    digest: str,
    params: tuple[object, ...],
) -> None:
    """Issue the idempotent INSERT and resolve a same-identity replay/conflict.

    ``ON CONFLICT (result_identity) DO NOTHING`` alone would silently swallow a
    *conflicting* replay (same identity, different payload) -- indistinguishable
    from a harmless retry. The SELECT-and-compare after a zero-row insert is what
    tells the two apart (ADR-043).
    """
    cur.execute(_APPEND_OPERATION_RESULT_INSERT_SQL, params)
    if cur.rowcount:
        return

    cur.execute(_APPEND_OPERATION_RESULT_SELECT_DIGEST_SQL, (result_identity,))
    row = cur.fetchone()
    existing_digest = row[0] if row else None
    if existing_digest == digest:
        # Harmless replay: same operation generation, same result content.
        return

    logger.warning(
        "%s result_identity_fp=%s",
        _OPERATION_RESULT_CONFLICT,
        safe_log_fingerprint(result_identity),
    )


def append_operation_result(
    *,
    operation_id: str,
    request_id: str,
    resource: str,
    operation: str,
    result_kind: str,
    result_payload: dict[str, Any],
    cur: psycopg.Cursor[tuple[object, ...]] | None = None,
) -> None:
    """Append a best-effort, versioned result to the operation result inbox.

    Mirrors :func:`enqueue_event_outbox`'s optional-cursor idiom: when ``cur`` is
    provided the INSERT rides the caller's transaction (wrapped in a SAVEPOINT so a
    shadow failure cannot poison it); when ``cur`` is None a dedicated connection
    is opened and committed immediately.

    ``result_identity`` is deterministic per operation generation + result kind
    (``f"{operation_id}:{result_kind}"``), so a retried provisioner run replays
    idempotently. Any failure -- including a conflicting replay -- is logged and
    swallowed, never raised.
    """
    try:
        envelope = build_operation_envelope(
            operation_id=operation_id,
            request_id=request_id,
            resource=resource,
            operation=operation,
            payload=result_payload,
        )
        digest = canonical_payload_digest(result_payload)
        result_identity = f"{envelope['operation_id']}:{result_kind}"
        params = (
            envelope["operation_id"],
            envelope["request_id"],
            resource,
            operation,
            envelope["contract_version"],
            result_kind,
            result_identity,
            digest,
            json.dumps(envelope),
        )

        if cur is not None:
            # SAVEPOINT: an unexpected error inside the shadow append rolls back
            # only this append, never the caller's authoritative write.
            with cur.connection.transaction():
                _insert_operation_result(cur, result_identity=result_identity, digest=digest, params=params)
        else:
            # Lazy import breaks the provisioner_db <-> provisioner_db_appends
            # cycle: provisioner_db re-exports these helpers, so it may not be
            # imported at this module's top.
            from provisioner_db import get_db_connection

            with get_db_connection() as conn:
                with conn.cursor() as _cur:
                    _insert_operation_result(_cur, result_identity=result_identity, digest=digest, params=params)
                conn.commit()
    except Exception:
        logger.warning(
            "%s operation_id_fp=%s result_kind=%s",
            _OPERATION_RESULT_APPEND_FAILED,
            safe_log_fingerprint(str(operation_id)),
            result_kind,
            exc_info=True,
        )


def append_range_provision_result(
    cur: psycopg.Cursor[tuple[object, ...]],
    ref: OperationRef | None,
    *,
    range_id: int,
    subnet_count: int,
    instance_count: int,
    ngfw_instance_id: int | None,
) -> None:
    """Shadow-append the terminal-success result of a range provision."""
    if ref is None or ref.operation_id is None:
        return
    append_operation_result(
        operation_id=ref.operation_id,
        request_id=ref.request_id,
        resource="range",
        operation="provision",
        result_kind="TERMINAL_SUCCESS",
        result_payload={
            "status": ResourceStatus.READY.value,
            "range_id": range_id,
            "subnet_count": subnet_count,
            "instance_count": instance_count,
            "ngfw_instance_id": ngfw_instance_id,
        },
        cur=cur,
    )


def append_range_destroy_result(
    cur: psycopg.Cursor[tuple[object, ...]],
    ref: OperationRef | None,
    *,
    range_id: int,
    instance_count: int,
    subnet_count: int,
) -> None:
    """Shadow-append the terminal-success result of a range destroy."""
    if ref is None or ref.operation_id is None:
        return
    append_operation_result(
        operation_id=ref.operation_id,
        request_id=ref.request_id,
        resource="range",
        operation="destroy",
        result_kind="TERMINAL_SUCCESS",
        result_payload={
            "status": ResourceStatus.DESTROYED.value,
            "range_id": range_id,
            "instances_destroyed": instance_count,
            "subnets_destroyed": subnet_count,
        },
        cur=cur,
    )
