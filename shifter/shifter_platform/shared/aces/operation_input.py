"""Closed ACES operation-input contract (ADR-043 phase 5, #1837).

``shared.operation_envelope`` owns the *transport* shape; this module owns the
bounded ``payload`` the Engine materializes into ``OperationInput`` for an
``aces-range`` generation, and the fail-closed parser the provisioner runs
before any cloud or guest mutation.

It replaces four direct domain-table reads (``mission_control_range`` +
``engine_request`` for the plan, ``engine_aces_content_delivery_binding``,
``engine_aces_image_mapping``, and ``engine_instance`` for backend ownership
evidence) with one immutable row selected by the canonical ``operation_id``.

The projection *composes* existing contracts rather than re-modelling them:

* the serialized ACES ``ProvisioningPlan`` already persisted in ``range_config``
  and parsed by the provisioner's fail-closed ``aces_plan.parse_plan``;
* ``shared.aces.content_delivery.DeliveryBinding`` transport, byte-free;
* the registry candidate shape consumed by
  ``shared.aces.image_policy.resolve_from_candidates``; and
* the normalized backend vocabulary from ``shared.range_instantiation_policy``.

Deliberately absent: ``user_id`` and any payload-owned ownership, ORM joins,
raw ``engine_instance.state`` evidence, registry management metadata (primary
keys, ``enabled`` flags, notes, timestamps), disabled rows, and the tenant
registry as a whole. Ownership is the locked generation plus ``request_id``.

Dependency-light on purpose: the standalone provisioner image imports this
without Django or the platform schema graph.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from shared.aces.content_delivery import ContentDeliveryError, DeliveryBinding

__all__ = [
    "MAX_DELIVERY_BINDINGS",
    "MAX_IMAGE_CANDIDATES",
    "MAX_IMAGE_KEYS",
    "AcesOperationInput",
    "AcesOperationInputError",
    "build_aces_operation_input",
    "candidate_key",
    "image_lookup_key",
    "parse_aces_operation_input",
    "plan_image_lookup_keys",
]


class AcesOperationInputError(Exception):
    """The ACES operation input is not a valid, bounded projection."""


# Bounded per ADR-043-R2/R7: the input is a reference-only projection, never a
# registry dump or a full topology snapshot.
MAX_DELIVERY_BINDINGS = 512
MAX_IMAGE_CANDIDATES = 64
MAX_IMAGE_KEYS = 256

_INPUT_KEYS = frozenset(
    {
        "plan",
        "delivery_bindings",
        "image_candidates",
        "range_backend",
        "instantiation_purpose",
        "legacy_range_id",
    }
)

# Exactly the registry columns the resolver consumes. Anything else -- row id,
# enabled flag, notes, timestamps -- is management metadata and stays server-side.
_CANDIDATE_KEYS = frozenset({"source_version", "image_ref", "machine_type", "disk_size_gb", "disk_type"})

# Mirrors ``shared.range_instantiation_policy`` without importing it: that module
# pulls settings/policy machinery the provisioner image does not load. The
# vocabulary is closed here so an unknown backend fails at the wire.
_VALID_RANGE_BACKENDS = frozenset({"gce", "gdc"})

_KEY_SEPARATOR = ":"
_NODE_RESOURCE_TYPE = "node"


def image_lookup_key(*, source_name: str | None, os_family: str | None) -> str:
    """Return the registry lookup key for one plan node.

    The authored ACES ``source`` keys the lookup; a source-less node falls back
    to its ``os_family`` so the backend can supply a base OS image (ADR-032
    base-OS policy).

    This is the *single* rule. The Engine uses it to scope which registry rows
    it projects; the provisioner uses it to resolve each node against that
    projection. If the two derivations ever diverge, the Engine omits a key the
    provisioner later asks for and the image silently goes missing at
    realization -- so both sides call this, and neither re-derives it.
    """
    return (source_name or None) or (os_family or None) or ""


def candidate_key(provider: str, source_name: str) -> str:
    """Return the transport key for one ``(provider, source_name)`` candidate set."""
    return f"{provider}{_KEY_SEPARATOR}{source_name}"


def _require(condition: bool, message: str) -> None:
    """Raise ``AcesOperationInputError(message)`` unless ``condition`` is true."""
    if not condition:
        raise AcesOperationInputError(message)


def _require_mapping(value: object, field: str) -> dict[str, Any]:
    """Return ``value`` as a mapping, else fail closed."""
    if not isinstance(value, Mapping):
        raise AcesOperationInputError(f"{field} must be an object")
    return dict(value)


def _require_exact_keys(value: Mapping[str, Any], allowed: frozenset[str], field: str) -> None:
    """Fail closed unless ``value`` carries exactly ``allowed``."""
    actual = frozenset(value)
    unexpected = sorted(actual - allowed)
    _require(not unexpected, f"{field} has unexpected field(s): {', '.join(unexpected)}")
    missing = sorted(allowed - actual)
    _require(not missing, f"{field} is missing field(s): {', '.join(missing)}")


def plan_image_lookup_keys(plan: object) -> tuple[str, ...]:
    """Return the distinct registry lookup keys a serialized plan references.

    Walks the serialized ``ProvisioningPlan`` resources, keeping node payloads
    only, and returns each node's :func:`image_lookup_key` in first-seen order
    with duplicates collapsed. Empty keys (a node with neither an authored
    source nor an ``os_family``) are dropped: they cannot select a registry row.
    """
    resources = _require_mapping(plan, "aces plan").get("resources")
    if resources is None:
        # A plan with no resources block yields no image lookups. Validating the
        # plan itself is the provisioner's fail-closed ``aces_plan.parse_plan``;
        # scoping the registry projection must not become a second plan parser.
        return ()
    if not isinstance(resources, Mapping):
        raise AcesOperationInputError("aces plan resources must be an object")

    keys: list[str] = []
    for address, resource in resources.items():
        entry = _require_mapping(resource, f"aces plan resource '{address}'")
        if entry.get("resource_type") != _NODE_RESOURCE_TYPE:
            continue
        payload = _require_mapping(entry.get("payload"), f"aces plan resource '{address}' payload")
        node_spec = _require_mapping(payload.get("spec"), f"aces plan resource '{address}' spec").get("node") or {}
        node = _require_mapping(node_spec, f"aces plan resource '{address}' node spec")
        key = image_lookup_key(
            source_name=_optional_str(node.get("source")), os_family=_optional_str(payload.get("os_family"))
        )
        if key and key not in keys:
            keys.append(key)
    return tuple(keys)


def _optional_str(value: object) -> str | None:
    """Return a non-empty string, or None for absent/blank/non-string values."""
    if isinstance(value, str) and value.strip():
        return value
    return None


@dataclass(frozen=True)
class AcesOperationInput:
    """The parsed, validated ACES input for one operation generation."""

    plan: dict[str, Any]
    delivery_bindings: tuple[DeliveryBinding, ...]
    range_backend: str | None
    instantiation_purpose: str | None
    legacy_range_id: int
    _image_candidates: dict[str, tuple[dict[str, Any], ...]]

    def image_candidates_for(self, provider: str, source_name: str) -> list[dict[str, Any]]:
        """Return the projected registry candidates for one lookup key.

        An empty list means the projection carried no enabled row for that key.
        That is a resolver concern -- the existing image policy fails loud at
        realization -- not a transport error.
        """
        return [dict(candidate) for candidate in self._image_candidates.get(candidate_key(provider, source_name), ())]

    def binding_transport(self) -> list[dict[str, Any]]:
        """Return the byte-free binding transport rows for the realization path."""
        return [binding.to_transport() for binding in self.delivery_bindings]


def _validated_backend(value: object) -> str | None:
    """Return a normalized range backend, or None when the range carries no binding."""
    if value is None:
        return None
    _require(isinstance(value, str), "aces operation input range_backend must be a string or null")
    _require(
        value in _VALID_RANGE_BACKENDS,
        f"aces operation input range_backend must be one of: {', '.join(sorted(_VALID_RANGE_BACKENDS))}",
    )
    return str(value)


def _validated_legacy_range_id(value: object) -> int:
    """Return the opaque legacy naming key, or fail closed.

    This integer reconstructs deterministic cloud-resource and secret names that
    ACES already derives from ``Range.id``. It is never used to select or
    authorize an Engine row -- ``request_id`` plus the locked generation do that.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise AcesOperationInputError("aces operation input legacy_range_id must be an int")
    if value <= 0:
        raise AcesOperationInputError("aces operation input legacy_range_id must be positive")
    return value


def _validated_bindings(value: object) -> tuple[DeliveryBinding, ...]:
    """Rebuild every delivery binding through its own closed parser."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise AcesOperationInputError("aces operation input delivery_bindings must be a list")
    rows = list(value)
    _require(
        len(rows) <= MAX_DELIVERY_BINDINGS,
        f"aces operation input carries more than {MAX_DELIVERY_BINDINGS} delivery bindings",
    )
    bindings: list[DeliveryBinding] = []
    for index, row in enumerate(rows):
        raw = _require_mapping(row, f"aces operation input delivery_bindings[{index}]")
        try:
            # DeliveryBinding.from_transport is the gate: it rejects unknown keys,
            # so a smuggled bucket / URL / signed-URL / bytes field cannot ride
            # along, and it re-validates the digest, key, and byte count.
            bindings.append(DeliveryBinding.from_transport(raw))
        except ContentDeliveryError as exc:
            raise AcesOperationInputError(f"aces operation input delivery_bindings[{index}]: {exc}") from None
    return tuple(bindings)


def _validated_candidate(raw: object, field: str) -> dict[str, Any]:
    """Return one registry candidate row closed on exactly the resolver's columns."""
    candidate = _require_mapping(raw, field)
    _require_exact_keys(candidate, _CANDIDATE_KEYS, field)
    image_ref = candidate["image_ref"]
    if not isinstance(image_ref, str) or not image_ref.strip():
        raise AcesOperationInputError(f"{field} image_ref is invalid")
    return candidate


def _validated_candidates(value: object) -> dict[str, tuple[dict[str, Any], ...]]:
    """Validate the ``(provider, source_name)``-keyed candidate projection."""
    raw = _require_mapping(value, "aces operation input image_candidates")
    _require(len(raw) <= MAX_IMAGE_KEYS, f"aces operation input carries more than {MAX_IMAGE_KEYS} image keys")

    projected: dict[str, tuple[dict[str, Any], ...]] = {}
    for key, rows in raw.items():
        _require(
            isinstance(key, str) and key.count(_KEY_SEPARATOR) == 1 and all(key.split(_KEY_SEPARATOR)),
            f"aces operation input image_candidates key '{key}' must be '<provider>:<source_name>'",
        )
        if not isinstance(rows, Sequence) or isinstance(rows, str | bytes):
            raise AcesOperationInputError(f"aces operation input image_candidates['{key}'] must be a list")
        entries = list(rows)
        _require(
            len(entries) <= MAX_IMAGE_CANDIDATES,
            f"aces operation input image_candidates['{key}'] carries more than {MAX_IMAGE_CANDIDATES} candidates",
        )
        projected[key] = tuple(
            _validated_candidate(entry, f"aces operation input image_candidates['{key}'][{index}]")
            for index, entry in enumerate(entries)
        )
    return projected


def parse_aces_operation_input(payload: object) -> AcesOperationInput:
    """Validate an ACES operation-input payload and return the parsed projection.

    Fails closed on an unexpected or missing key, a non-mapping payload, a
    tampered or over-claiming delivery binding, an unbounded collection, a
    registry row carrying management metadata, a malformed candidate key, an
    unknown backend, or a non-positive legacy naming key.
    """
    obj = _require_mapping(payload, "aces operation input")
    _require_exact_keys(obj, _INPUT_KEYS, "aces operation input")
    return AcesOperationInput(
        plan=_require_mapping(obj["plan"], "aces operation input plan"),
        delivery_bindings=_validated_bindings(obj["delivery_bindings"]),
        range_backend=_validated_backend(obj["range_backend"]),
        instantiation_purpose=_optional_str(obj["instantiation_purpose"]),
        legacy_range_id=_validated_legacy_range_id(obj["legacy_range_id"]),
        _image_candidates=_validated_candidates(obj["image_candidates"]),
    )


def build_aces_operation_input(
    *,
    plan: Mapping[str, Any],
    delivery_bindings: Sequence[DeliveryBinding],
    image_candidates: Mapping[str, Sequence[Mapping[str, Any]]],
    range_backend: str | None,
    instantiation_purpose: str | None,
    legacy_range_id: int,
) -> dict[str, Any]:
    """Compose and validate the ACES operation-input payload in one step.

    Returns the JSON-serialisable payload for ``build_operation_envelope``.
    Candidate sets are emitted in sorted key order so the same generation
    materializes byte-identical input on a retry.
    """
    payload = {
        "plan": dict(plan),
        "delivery_bindings": [binding.to_transport() for binding in delivery_bindings],
        "image_candidates": {key: [dict(row) for row in image_candidates[key]] for key in sorted(image_candidates)},
        "range_backend": range_backend,
        "instantiation_purpose": instantiation_purpose,
        "legacy_range_id": legacy_range_id,
    }
    parse_aces_operation_input(payload)
    return payload
