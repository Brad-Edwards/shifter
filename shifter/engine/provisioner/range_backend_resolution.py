"""Resolve the per-operation GCP range backend binding (#1666, ADR-043).

Split out of ``terraform_ops.py`` (Sonar S104). This is the ownership question
asked once at operation start: which backend does this range actually belong to?

The evidence itself lives Engine-side now -- ADR-043 phase 5 (#1837) moved the
``engine_instance.state`` sweep there, since the Engine owns those rows -- so
this module reads only the normalized outcome off the immutable operation
input. It never guesses from the mutable ``GCP_RANGE_BACKEND`` selector: after a
``gdc -> gce`` flip that would strand a legacy range.

It also hosts :func:`prerequisite_error`, the ADR-039 fail-closed denial builder,
because that lives naturally beside the ``PREREQUISITE_DENIAL_CODE`` policy
constant it wraps and is shared with ``terraform_ops``.
"""

from __future__ import annotations

import logging
from typing import Any

from shared.range_instantiation_policy import PREREQUISITE_DENIAL_CODE, normalize_gcp_range_backend

from cloud.exceptions import CloudError
from config import resolve_cloud_provider
from provisioner_db_operation_input import OperationInputError, get_operation_input

logger = logging.getLogger(__name__)

__all__ = ["prerequisite_error", "resolve_operation_backend"]


def prerequisite_error(message: str) -> CloudError:
    """Build a fail-closed ADR-039 ``prerequisite`` CloudError with an authored message."""
    error = CloudError(message)
    error.code = PREREQUISITE_DENIAL_CODE
    return error


def _resolve_legacy_gcp_backend(range_data: dict[str, Any], operation_id: str | None) -> str:
    """Resolve a GCP range with no persisted binding from durable ownership evidence (#1666).

    A pre-#1666 (legacy) range carries no ownership binding. On destroy/reconcile
    we must never guess the backend from the mutable env selector -- after a
    ``gdc -> gce`` flip that would strand the range. The evidence is the
    provider/asset discriminant persisted on the range's ``engine_instance.state``
    rows; ADR-043 phase 5 (#1837) moved that evaluation to the Engine, which owns
    those rows, so only the normalized outcome crosses the operation boundary.
    An ambiguous, evidence-free, or unavailable outcome fails closed with a
    ``prerequisite`` diagnostic and retains its cleanup state for explicit repair.
    """
    request_id = range_data["request_id"]
    resolved = _legacy_backend_from_operation_input(operation_id, request_id)
    if resolved is not None:
        logger.info(
            "Resolved legacy GCP range backend from ownership evidence request_id=%s backend=%s",
            request_id,
            resolved,
        )
        return resolved
    raise prerequisite_error(
        "This GCP range predates backend ownership binding and its backend could not be proven from "
        "durable ownership evidence. Back-fill its range_backend with the operator command "
        "(manage.py backfill_range_backend_binding) while the historical selector is known, then retry. "
        "The range's cleanup state is retained; no resources were touched."
    )


def _legacy_backend_from_operation_input(operation_id: str | None, request_id: str) -> str | None:
    """Return the Engine-resolved legacy backend for this generation, or None.

    The input is bound to BOTH halves of the generation identity: an operation id
    from another request must not be able to supply the backend that routes this
    range's teardown. Returns ``None`` -- which the caller turns into a
    fail-closed ``prerequisite`` denial -- when there is no canonical generation
    to read an input for, when the input cannot be read or does not belong to
    this request, or when the Engine could not prove the backend. Guessing from
    the mutable selector is exactly what #1666 forbids.
    """
    if not operation_id:
        return None
    try:
        validated = get_operation_input(
            operation_id=operation_id, request_id=request_id, resource="range", operation="destroy"
        )
    except OperationInputError:
        logger.warning("Operation input unavailable for legacy backend resolution; failing closed")
        return None
    backend = validated.payload.get("legacy_range_backend")
    return str(backend) if backend else None


def resolve_operation_backend(range_data: dict[str, Any], operation: str, operation_id: str | None) -> str | None:
    """Resolve the per-operation GCP range backend from persisted ownership (#1666).

    Returns the normalized write-once binding when present; ``None`` for non-GCP
    (AWS) ranges, where gce/gdc routing does not apply. For a GCP range with no
    persisted binding, a destroy/reconcile resolves from durable ownership
    evidence (or fails closed). A normal provision must already carry the
    admission-time binding and never falls back to the deploy-wide selector.
    """
    persisted = range_data.get("range_backend")
    if persisted:
        return normalize_gcp_range_backend(persisted)
    if resolve_cloud_provider() != "gcp":
        return None
    # Re-reading the mutable selector here would allow an in-flight selector
    # flip to change ownership after admission.
    if operation != "destroy":
        raise prerequisite_error(
            "This GCP range has no persisted backend ownership binding; retry the launch after admission"
        )
    return _resolve_legacy_gcp_backend(range_data, operation_id)
