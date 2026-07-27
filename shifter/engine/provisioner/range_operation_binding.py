"""Per-operation range ownership binding: which backend and purpose this operation runs under.

Extracted from ``terraform_ops`` (Sonar S104) when issue #1354 added purpose
resolution beside the #1666 backend resolution. Everything here answers one
question -- what did the platform admit for this range, and does the current
deployment still match it -- before any provider call is made.

The binding is read from the locked Engine row projected by ``provisioner_db``.
It is never taken from argv, the Job environment, scenario content, or a
caller-supplied argument, and it is never re-derived from the mutable
``GCP_RANGE_BACKEND`` selector after admission (ADR-030 / ADR-039).
"""

from __future__ import annotations

import logging
from typing import Any

from shared.range_instantiation_policy import (
    PREREQUISITE_DENIAL_CODE,
    InstantiationPurpose,
    normalize_gcp_range_backend,
    parse_instantiation_purpose,
)

from cloud.exceptions import CloudError
from config import get_gcp_range_backend, resolve_cloud_provider
from range_backend_evidence import resolve_legacy_range_backend

logger = logging.getLogger(__name__)


def prerequisite_error(message: str) -> CloudError:
    """Build a fail-closed ADR-039 ``prerequisite`` CloudError with an authored message."""
    error = CloudError(message)
    error.code = PREREQUISITE_DENIAL_CODE
    return error


def _resolve_legacy_gcp_backend(range_data: dict[str, Any]) -> str:
    """Resolve a GCP range with no persisted binding from durable ownership evidence (#1666).

    A pre-#1666 (legacy) range carries no ownership binding. On destroy/reconcile
    we must never guess the backend from the mutable env selector -- after a
    ``gdc -> gce`` flip that would strand the range. Resolve only from durable,
    ownership-proven evidence (provider/asset discriminants persisted on the
    range's ``engine_instance.state`` rows, or an explicit operator backfill of
    the binding). An ambiguous or evidence-free row fails closed with a
    ``prerequisite`` diagnostic and retains its cleanup state for explicit repair.
    """
    request_id = range_data["request_id"]
    resolved = resolve_legacy_range_backend(request_id)
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


def resolve_operation_backend(range_data: dict[str, Any], operation: str) -> str | None:
    """Resolve the per-operation GCP range backend from persisted ownership (#1666).

    Returns the normalized write-once binding when present; ``None`` for non-GCP
    (AWS) ranges, where gce/gdc routing does not apply. For a GCP range with no
    persisted binding, a destroy/reconcile resolves from durable ownership
    evidence (or fails closed); provision (and its immediate compensation) fall
    back to the env selector, since a fresh range has no resources to disambiguate
    and the selector still equals what was admitted in that window.
    """
    persisted = range_data.get("range_backend")
    if persisted:
        return normalize_gcp_range_backend(persisted)
    # No binding: non-GCP ranges and the provision path (a fresh range with no
    # resources to disambiguate) fall back to the env selector; only a GCP
    # destroy/reconcile of a legacy range must resolve from durable evidence.
    if resolve_cloud_provider() != "gcp" or operation != "destroy":
        return None
    return _resolve_legacy_gcp_backend(range_data)


def resolve_provision_purpose(range_data: dict[str, Any], operation: str) -> InstantiationPurpose:
    """Resolve the trusted instantiation purpose for a provision (#1354).

    Purpose is *provision-only* authority: it gates new cloud mutation and is not
    read anywhere on the teardown path. Destroy therefore never parses it and
    returns the ``RangeOperation`` default unexamined -- teardown routes solely
    from persisted backend ownership (#1666), so a damaged, forward-version, or
    rolled-back purpose value can never strand owned resources.

    On a provision, a NULL binding (legacy pre-#1666 and non-GCP rows) resolves to
    live-fire, the strictest reading of "no recorded purpose", and an unrecognized
    stored value is a ``prerequisite`` fault rather than a reason to guess.
    """
    if operation != "up":
        return InstantiationPurpose.LIVE_FIRE
    try:
        return parse_instantiation_purpose(range_data.get("instantiation_purpose"))
    except ValueError as exc:
        raise prerequisite_error(f"This range's persisted instantiation purpose is not a known value: {exc}") from exc


def assert_provision_route(backend: str | None, operation: str) -> None:
    """Refuse to provision when the binding no longer matches the deploy selector (#1354).

    CMS admitted a specific (backend, purpose) pair and the Engine persisted it.
    If the deploy-wide ``GCP_RANGE_BACKEND`` selector has since changed, the
    provision route would no longer be the one policy approved -- so fail closed
    with a ``prerequisite`` diagnostic instead of silently realizing the range on
    a different substrate. Destroy is exempt: teardown routes from ownership by
    design (#1666), so an owned range stays destroyable after a selector flip.
    """
    if operation != "up" or backend is None or resolve_cloud_provider() != "gcp":
        return
    selector = get_gcp_range_backend()
    if backend != selector:
        raise prerequisite_error(
            f"This range was admitted for range backend '{backend}' but the deployment now selects "
            f"'{selector}'. Provisioning stopped before any cloud mutation; restore the admitted "
            "selector or recreate the range under the current one."
        )
