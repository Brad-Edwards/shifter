"""Compose the immutable operation-input projection (ADR-043).

Split out of ``launch_intents.py`` (Sonar S104), which grew past the file-size
budget when the ACES family was cut over in phase 5 (#1837). ``launch_intents``
owns *authorizing and minting* an operation generation; this module owns *what
that generation's immutable input contains*.

The projection is reference-only: it composes the existing persisted contracts
(the serialized ACES plan, ``DeliveryBinding``, the registry candidate shape,
the normalized backend vocabulary) rather than dumping ORM rows. Payload-owned
ownership, raw ``engine_instance.state``, and registry management metadata do
not cross the boundary.
"""

from __future__ import annotations

import json

from engine.models import Instance, Range, Request
from shared.aces.content_delivery import DeliveryBinding
from shared.aces.operation_input import build_aces_operation_input, candidate_key, plan_image_lookup_keys

__all__ = ["operation_input_payload"]


# Durable ownership discriminants persisted on ``engine_instance.state`` (#1666).
# Mirrors the resolution the provisioner used to perform itself; the Engine owns
# these rows, so it evaluates the evidence and ships only the normalized outcome.
_GDC_ASSET_TYPES = frozenset({"vm_runtime_vm", "scenario_pod"})
_GCE_ASSET_TYPE = "gce_vm"


def _resolve_backend_from_evidence(request: Request) -> str | None:
    """Resolve a legacy (NULL-binding) range's backend from ownership evidence.

    Returns the proven backend only when the evidence is unambiguous (exactly
    one backend across all request-owned instances); returns ``None`` for an
    empty, mixed, or unrecognized set so the consumer fails closed. Names,
    scenario shape, the current selector, and successful VM boot are not
    evidence -- after a ``gdc -> gce`` flip, guessing strands the range.
    """
    backends: set[str] = set()
    for state in Instance.objects.filter(request=request).values_list("state", flat=True):
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except (TypeError, ValueError):
                continue
        if not isinstance(state, dict):
            continue
        asset_type = str(state.get("asset_type", "")).strip()
        if asset_type == _GCE_ASSET_TYPE:
            backends.add("gce")
        elif asset_type in _GDC_ASSET_TYPES:
            backends.add("gdc")
    return next(iter(backends)) if len(backends) == 1 else None


def _resolved_range_backend(target: Range, request: Request) -> str | None:
    """Return the range's normalized backend binding, else the proven legacy one.

    ``request`` is the one already resolved by the caller, so the legacy
    evidence sweep is always scoped to a real request rather than re-deriving a
    nullable relation here.
    """
    if target.range_backend:
        return str(target.range_backend)
    return _resolve_backend_from_evidence(request)


def _aces_image_candidates(plan: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    """Project the enabled registry rows this plan can actually ask for.

    Scoped to the plan's own lookup keys (ADR-032-R2 authored source, else the
    node's OS family) so the tenant-wide registry never crosses the boundary
    wholesale. Disabled rows are excluded exactly as the direct
    ``WHERE enabled = TRUE`` read did, keeping a retired mapping fail-loud at
    realization.
    """
    from engine.models import AcesImageMapping

    keys = plan_image_lookup_keys(plan)
    if not keys:
        return {}
    projected: dict[str, list[dict[str, object]]] = {}
    rows = AcesImageMapping.objects.filter(source_name__in=list(keys), enabled=True).order_by(
        "provider", "source_name", "source_version"
    )
    for row in rows:
        projected.setdefault(candidate_key(str(row.provider), str(row.source_name)), []).append(
            {
                "source_version": row.source_version,
                "image_ref": row.image_ref,
                "machine_type": row.machine_type,
                "disk_size_gb": row.disk_size_gb,
                "disk_type": row.disk_type,
            }
        )
    return projected


def _aces_delivery_bindings(target: Range) -> list[DeliveryBinding]:
    """Rebuild this range's byte-free delivery bindings for transport."""
    from engine.models import AcesContentDeliveryBinding

    bindings = []
    for row in AcesContentDeliveryBinding.objects.filter(range=target).order_by("pk"):
        bindings.append(
            DeliveryBinding(
                content_address=row.content_address or None,
                sha256=row.sha256,
                storage_key=row.storage_key,
                byte_count=row.byte_count,
                binding_version=row.binding_version,
                resource_type=row.resource_type or None,
                resource_address=row.resource_address or None,
                payload_kind=row.payload_kind or None,
                install_policy=row.install_policy or None,
            )
        )
    return bindings


def _aces_input_payload(target: Range, request: Request) -> dict[str, object]:
    """Compose the ACES operation input (ADR-043 phase 5, #1837).

    Replaces four direct provisioner reads with one immutable row: the
    serialized plan, the delivery bindings, the plan-scoped image candidates,
    and the normalized backend ownership.
    """
    plan = target.range_config or {}
    return build_aces_operation_input(
        plan=plan,
        delivery_bindings=_aces_delivery_bindings(target),
        image_candidates=_aces_image_candidates(plan),
        range_backend=_resolved_range_backend(target, request),
        instantiation_purpose=target.instantiation_purpose or None,
        legacy_range_id=target.id,
    )


def operation_input_payload(target: Range | Instance, resource: str, request: Request) -> dict[str, object]:
    """Compose the immutable operation-input projection from engine-owned models.

    A reference-only projection of the existing persisted contracts, not an ORM
    dump. The ACES family consumes the full projection (#1837); the cyberscript
    range family still reads most of its inputs directly, and takes only the
    normalized legacy backend it can no longer resolve for itself.
    """
    if isinstance(target, Range):
        if resource == "aces-range":
            return _aces_input_payload(target, request)
        return {
            "range_spec": target.range_config or {},
            "legacy_range_backend": _resolved_range_backend(target, request),
        }
    return {"role": str(target.role), "os_type": str(target.os_type)}
