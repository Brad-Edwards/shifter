"""Write-once range-backend ownership binding helpers (#1666).

Shared by the cyberscript (:mod:`engine.services._range`) and ACES
(:mod:`engine.services._aces_range`) create services: map the trusted CMS
``BackendAdmission`` to the Range binding columns and enforce write-once
ownership on an idempotent create reuse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from shared.range_instantiation_policy import (
    InstantiationPurpose,
    evaluate_gcp_backend_admission,
    normalize_gcp_range_backend,
)

from ._common import EngineError

if TYPE_CHECKING:
    from engine.models import Range
    from shared.range_instantiation_policy import BackendAdmission


def require_workspace_binding(workspace_id: int | None) -> None:
    """Refuse to create a range with no tenancy scope (#1325, ADR-046-R3).

    The binding is supplied by the trusted CMS launch facade, like
    ``BackendAdmission`` above. Accepting ``None`` here would let a new or
    refactored caller persist an unscoped range indistinguishable from a legacy
    pre-#1325 row -- the ambiguity the non-null scope columns and this guard
    exist to remove.
    """
    if workspace_id is None:
        raise EngineError("A range cannot be created without a workspace binding")


def backend_binding_fields(backend_admission: BackendAdmission | None) -> dict[str, str]:
    """Map an admitted ``BackendAdmission`` to the write-once Range binding columns.

    Returns ``{}`` for a non-GCP launch (``backend_admission is None``) so the
    columns stay NULL.

    ``BackendAdmission`` is a plain constructible dataclass, so ``admitted=True``
    from an arbitrary in-process caller is not by itself authority (#1354). The
    pair is re-evaluated here against the closed default-deny policy -- without
    rereading the environment selector -- so a fabricated, denied, or malformed
    pair can never be persisted as ownership. This is the Engine-side half of the
    admission the CMS service boundary already performed.
    """
    if backend_admission is None:
        return {}
    try:
        backend = normalize_gcp_range_backend(backend_admission.backend)
        purpose = InstantiationPurpose(backend_admission.purpose)
    except ValueError as exc:
        raise EngineError(f"Range backend binding is not a closed policy value: {exc}") from exc
    admission = evaluate_gcp_backend_admission(backend, None, purpose)
    if not admission.admitted:
        raise EngineError(f"Range backend binding is not admitted by policy: {admission.reason}")
    return {"range_backend": backend, "instantiation_purpose": purpose.value}


def verify_existing_binding(
    existing_range: Range,
    request_id: UUID,
    backend_admission: BackendAdmission | None,
) -> None:
    """Enforce write-once binding on an idempotent create reuse.

    Idempotent create with the same request must carry the same binding; a
    *different* already-persisted binding is an ADR-039 ``conflict``, never a
    silent update. A NULL persisted binding (legacy row) is left untouched here --
    legacy repair is a destroy-time, ownership-evidence concern, not a create-time
    rewrite.
    """
    if backend_admission is None or not existing_range.range_backend:
        return
    expected = backend_binding_fields(backend_admission)
    if (
        existing_range.range_backend != expected["range_backend"]
        or existing_range.instantiation_purpose != expected["instantiation_purpose"]
    ):
        raise EngineError(
            f"Range backend binding conflict for request {request_id}: persisted "
            f"{existing_range.range_backend}/{existing_range.instantiation_purpose} differs from admitted "
            f"{expected['range_backend']}/{expected['instantiation_purpose']} (ADR-039 conflict; write-once)"
        )
