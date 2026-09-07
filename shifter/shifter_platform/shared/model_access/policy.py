"""Fail-closed profile intersection for scenario and deployment policy."""

from __future__ import annotations

from shared.model_access.catalog import ContractError
from shared.model_access.models import EffectiveProfile, ModelProfile, ScenarioNeed


def intersect_profile(profile: ModelProfile, need: ScenarioNeed) -> EffectiveProfile | None:
    if profile.profile_id != need.profile_id:
        raise ContractError("policy.profile_mismatch", "profile_id")
    available = set(profile.capabilities) & set(need.allowed_capabilities)
    missing = set(need.required_capabilities) - available
    if missing:
        if not need.required:
            return None
        raise ContractError("policy.required_capability_unavailable", "required_capabilities")
    strategies = tuple(item for item in profile.allowed_strategies if item in need.allowed_strategies)
    regions = tuple(item for item in profile.data_regions if item in need.data_regions)
    if not available or not strategies or not regions:
        if not need.required:
            return None
        raise ContractError("policy.empty_intersection")
    try:
        limits = profile.limits.tightened_with(need.limits)
    except ValueError as exc:
        if not need.required:
            return None
        raise ContractError("policy.currency_mismatch", "limits.currency") from exc
    return EffectiveProfile(
        profile_id=profile.profile_id,
        required=need.required,
        capabilities=tuple(item for item in profile.capabilities if item in available),
        allowed_strategies=strategies,
        data_regions=regions,
        limits=limits,
    )
