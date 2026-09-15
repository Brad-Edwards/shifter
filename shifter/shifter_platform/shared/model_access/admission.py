"""Fail-closed required-model admission decision (PLAT-202, #2119).

The PLAT-201 capacity helpers are deliberately best-effort: ``None`` means
"proceed". Required model access is different — it is an authorization decision
that must fail closed before dispatch. This module is the single pure decision
every launch family routes required model access through. It performs no I/O:
the Engine resolves the scenario-need projection, deployment profile, folded
sharing admissibility, egress posture and authority availability, then calls
``decide_model_admission`` to obtain a bounded, secret-free result.

Sharing overlap resolution itself lives in :mod:`shared.model_access.effective_policy`;
here ``sharing_admissible`` is its folded verdict so this module never restates
the precedence rules.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import StrictBool

from shared.model_access.catalog import ContractError
from shared.model_access.core_models import (
    AllocationStrategy,
    ClosedModel,
    EffectiveProfile,
    Identifier,
    ModelProfile,
    PositiveInt,
    ScenarioNeed,
)
from shared.model_access.policy import intersect_profile


class ModelAdmissionOutcome(StrEnum):
    """The three admission outcomes; only ADMITTED permits a launch to proceed."""

    ADMITTED = "admitted"
    DENIED = "denied"
    INDETERMINATE = "indeterminate"


class ModelAdmissionReason(StrEnum):
    """Bounded, safe reason codes; never a raw provider error or rejected value."""

    ADMITTED = "admitted"
    NO_BINDING = "no_binding"
    OPTIONAL_ABSENT = "optional_absent"
    DIGEST_MISMATCH = "digest_mismatch"
    POLICY_UNAVAILABLE = "policy_unavailable"
    EGRESS_INCOMPATIBLE = "egress_incompatible"
    REQUIRED_CAPABILITY_UNAVAILABLE = "required_capability_unavailable"
    EMPTY_INTERSECTION = "empty_intersection"
    CURRENCY_MISMATCH = "currency_mismatch"
    SHARING_CONFLICT = "sharing_conflict"
    AUTHORITY_UNAVAILABLE = "authority_unavailable"
    STRATEGY_NOT_ALLOWED = "strategy_not_allowed"


class EventModelDemand(ClosedModel):
    """Organizer-authored typed model demand for one workload role (CTF-908).

    Constrained by the scenario need and the deployment/event envelope. It never
    names a provider, account, project, region, credential, endpoint, shard, or
    price — those stay deployment-owned in the catalog.
    """

    workload_role: Identifier
    expected_concurrency: PositiveInt
    per_participant_requests: PositiveInt
    per_participant_input_tokens: PositiveInt
    per_participant_output_tokens: PositiveInt
    allowed_strategy: AllocationStrategy


class ScenarioNeedProjection(ClosedModel):
    """A workload's authored scenario need plus its digest-verification status.

    ``need`` is ``None`` only when no binding is authored for the workload.
    ``digest_verified`` is whether the registered pack's current digest matches
    the digest the need was authored against; a required need over unverified
    content fails closed.
    """

    workload_role: Identifier
    need: ScenarioNeed | None
    digest_verified: StrictBool


class ModelAdmissionResult(ClosedModel):
    """Bounded, secret-free required-model admission decision for one workload."""

    workload_role: Identifier
    outcome: ModelAdmissionOutcome
    reason: ModelAdmissionReason
    required: StrictBool


_CONTRACT_ERROR_REASONS = {
    "policy.required_capability_unavailable": ModelAdmissionReason.REQUIRED_CAPABILITY_UNAVAILABLE,
    "policy.empty_intersection": ModelAdmissionReason.EMPTY_INTERSECTION,
    "policy.currency_mismatch": ModelAdmissionReason.CURRENCY_MISMATCH,
    "policy.profile_mismatch": ModelAdmissionReason.POLICY_UNAVAILABLE,
}


def decide_model_admission(
    *,
    projection: ScenarioNeedProjection,
    demand: EventModelDemand | None,
    profile: ModelProfile | None,
    sharing_admissible: bool | None,
    egress_permits_model: bool,
    authority_available: bool,
    sharing_profile: EffectiveProfile | None = None,
) -> ModelAdmissionResult:
    """Decide required-model admission for one workload, failing closed.

    ``sharing_admissible`` is the folded verdict of the sharing overlap compiler:
    ``None`` when no sharing binding applies, ``True``/``False`` otherwise.
    ``sharing_profile`` is the compiled effective profile of an admissible sharing
    overlap (when it carries a profile facet); the need is intersected against it
    so a sharing restriction actually tightens the decision.
    """
    role = projection.workload_role
    need = projection.need

    # No authored binding: this workload carries no required-model gate.
    if need is None:
        return _result(role, ModelAdmissionOutcome.ADMITTED, ModelAdmissionReason.NO_BINDING, required=False)

    required = bool(need.required)

    # A binding authored against different pack content cannot be trusted.
    if not projection.digest_verified:
        return _closed_or_absent(role, required, ModelAdmissionReason.DIGEST_MISMATCH)

    # Required access needs a live authority to prove membership and policy.
    if not authority_available:
        if required:
            return _result(
                role, ModelAdmissionOutcome.INDETERMINATE, ModelAdmissionReason.AUTHORITY_UNAVAILABLE, required=True
            )
        return _absent(role)

    # Deployment policy must offer the referenced profile.
    if profile is None:
        return _closed_or_absent(role, required, ModelAdmissionReason.POLICY_UNAVAILABLE)

    # Zero-egress is incompatible with required external model use.
    if not egress_permits_model:
        return _closed_or_absent(role, required, ModelAdmissionReason.EGRESS_INCOMPATIBLE)

    # Intersect the scenario need with the deployment profile (fail-closed for required).
    try:
        effective = intersect_profile(profile, need)
    except ContractError as exc:
        reason = _CONTRACT_ERROR_REASONS.get(exc.code, ModelAdmissionReason.POLICY_UNAVAILABLE)
        return _closed_or_absent(role, required, reason)
    if effective is None:
        # Optional need with an empty intersection: explicit visible unavailability.
        return _absent(role)

    # A required grant needs the sharing overlap to resolve cleanly.
    if sharing_admissible is False:
        return _closed_or_absent(role, required, ModelAdmissionReason.SHARING_CONFLICT)

    # An admissible sharing overlap still restricts: intersect the need against
    # its compiled effective profile, not just the catalog profile.
    strategies = set(effective.allowed_strategies)
    if sharing_profile is not None:
        restricted = _restrict_by_sharing(effective, need, sharing_profile)
        if restricted is not None:
            return _closed_or_absent(role, required, restricted)
        strategies &= set(sharing_profile.allowed_strategies)

    # The organizer's selected strategy must stay within the effective envelope.
    if demand is not None and demand.allowed_strategy not in strategies:
        return _closed_or_absent(role, required, ModelAdmissionReason.STRATEGY_NOT_ALLOWED)

    return _result(role, ModelAdmissionOutcome.ADMITTED, ModelAdmissionReason.ADMITTED, required=required)


def _restrict_by_sharing(
    effective: EffectiveProfile,
    need: ScenarioNeed,
    sharing_profile: EffectiveProfile,
) -> ModelAdmissionReason | None:
    """Return a denial reason when the sharing overlap forecloses the need, else None.

    The sharing overlap must reference the same profile and must still leave every
    required capability available and a non-empty region/strategy intersection
    once combined with the catalog-intersected envelope.
    """
    if sharing_profile.profile_id != need.profile_id:
        return ModelAdmissionReason.SHARING_CONFLICT
    capabilities = set(effective.capabilities) & set(sharing_profile.capabilities)
    # An empty capability intersection is never admissible, even when the need
    # lists no *required* capabilities (a subset check trivially passes for an
    # empty required set). intersect_profile and the sharing compiler both reject
    # this; preserve the invariant when combining the two envelopes.
    if not capabilities:
        return ModelAdmissionReason.EMPTY_INTERSECTION
    if not set(need.required_capabilities).issubset(capabilities):
        return ModelAdmissionReason.REQUIRED_CAPABILITY_UNAVAILABLE
    if not (set(effective.data_regions) & set(sharing_profile.data_regions)):
        return ModelAdmissionReason.EMPTY_INTERSECTION
    if not (set(effective.allowed_strategies) & set(sharing_profile.allowed_strategies)):
        return ModelAdmissionReason.EMPTY_INTERSECTION
    return None


def _result(
    role: str, outcome: ModelAdmissionOutcome, reason: ModelAdmissionReason, *, required: bool
) -> ModelAdmissionResult:
    """Build one admission result."""
    return ModelAdmissionResult(workload_role=role, outcome=outcome, reason=reason, required=required)


def _closed_or_absent(role: str, required: bool, reason: ModelAdmissionReason) -> ModelAdmissionResult:
    """Deny a required need; render an optional one as explicit visible absence."""
    if required:
        return _result(role, ModelAdmissionOutcome.DENIED, reason, required=True)
    return _absent(role)


def _absent(role: str) -> ModelAdmissionResult:
    """Optional access is absent as an explicit, non-blocking admitted outcome."""
    return _result(role, ModelAdmissionOutcome.ADMITTED, ModelAdmissionReason.OPTIONAL_ABSENT, required=False)


__all__ = [
    "EventModelDemand",
    "ModelAdmissionOutcome",
    "ModelAdmissionReason",
    "ModelAdmissionResult",
    "ScenarioNeedProjection",
    "decide_model_admission",
]
