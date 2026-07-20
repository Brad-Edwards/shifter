"""Fail-closed validation and ordering for ACES delivery resources."""

from __future__ import annotations

import re
from typing import Any

from shared.aces.content_delivery import ContentDeliveryError, DeliveryBinding

from aces_gcp_composition import AcesGceCompositionError
from aces_plan import AcesPlan, AcesPlanContent, AcesPlanFeature

SUPPORTED_DELIVERY_CONTENT_TYPES = frozenset({"file", "directory"})
SAFE_SERVICE_IDENTITY = re.compile(r"^[A-Za-z0-9._+-]+$")


class FeatureDependencyCycleError(RuntimeError):
    """Raised when feature ordering cannot be resolved."""


def source_backed_content(aces_plan: AcesPlan) -> list[AcesPlanContent]:
    """Return every content item whose bytes are delivered, not baked in."""
    return [item for item in aces_plan.content if item.source_name]


def source_backed_features(aces_plan: AcesPlan) -> list[AcesPlanFeature]:
    """Return delivered artifact/configuration features (services are separate)."""
    return [
        item for item in aces_plan.features if item.source_name and item.feature_type in {"artifact", "configuration"}
    ]


def ordered_features(aces_plan: AcesPlan) -> list[AcesPlanFeature]:
    """Return features in stable dependency order, failing closed on a cycle."""
    features = list(aces_plan.features)
    feature_addresses = {feature.address for feature in features}
    remaining = {
        feature.address: {dependency for dependency in feature.ordering_dependencies if dependency in feature_addresses}
        for feature in features
    }
    ordered: list[AcesPlanFeature] = []
    completed: set[str] = set()
    while len(ordered) < len(features):
        ready = [
            feature
            for feature in features
            if feature.address not in completed and remaining[feature.address].issubset(completed)
        ]
        if not ready:
            raise FeatureDependencyCycleError("ACES feature realization dependencies contain a cycle")
        ordered.extend(ready)
        completed.update(feature.address for feature in ready)
    return ordered


def validated_binding(raw: dict[str, Any]) -> DeliveryBinding:
    """Parse and validate one persisted delivery binding."""
    binding = DeliveryBinding.from_transport(raw)
    suffix = f"/{binding.sha256[:2]}/{binding.sha256}"
    if not binding.storage_key.endswith(suffix):
        raise ContentDeliveryError("delivery binding storage_key is not content-addressed")
    return binding


def _validate_feature(feature: AcesPlanFeature) -> None:
    if feature.feature_type not in {"service", "artifact", "configuration"}:
        raise AcesGceCompositionError("feature type has no provisioner realization")
    if feature.has_environment:
        raise AcesGceCompositionError("feature environment has no safe realization contract")
    if not feature.source_name:
        raise AcesGceCompositionError("feature source identity is missing")
    if feature.feature_type in {"artifact", "configuration"} and not feature.destination:
        raise AcesGceCompositionError("delivered feature destination is missing")
    unsafe_service_identity = feature.feature_type == "service" and (
        not SAFE_SERVICE_IDENTITY.fullmatch(feature.source_name)
        or (feature.source_version is not None and not SAFE_SERVICE_IDENTITY.fullmatch(feature.source_version))
    )
    if unsafe_service_identity:
        raise AcesGceCompositionError("service feature identity is invalid")


def _validated_bindings(delivery_bindings: list[dict[str, Any]] | None) -> list[DeliveryBinding]:
    validated: list[DeliveryBinding] = []
    for raw_binding in delivery_bindings or []:
        try:
            validated.append(validated_binding(raw_binding))
        except ContentDeliveryError:
            raise AcesGceCompositionError("a delivery binding failed contract validation") from None
    return validated


def assert_content_delivery_bindings_complete(
    aces_plan: AcesPlan,
    delivery_bindings: list[dict[str, Any]] | None,
) -> None:
    """Fail closed unless every deliverable resource has exactly one binding."""
    source_content = source_backed_content(aces_plan)
    source_features = source_backed_features(aces_plan)
    for feature in aces_plan.features:
        _validate_feature(feature)
    for item in source_content:
        if item.content_type not in SUPPORTED_DELIVERY_CONTENT_TYPES:
            raise AcesGceCompositionError(f"source-backed content {item.content_type!r} has no delivery materializer")

    validated = _validated_bindings(delivery_bindings)
    bound_identities = {
        (binding.resource_type or "content-placement", binding.resource_address or binding.content_address or "")
        for binding in validated
    }
    source_identities = {
        *(("content-placement", item.address) for item in source_content),
        *(("feature-binding", item.address) for item in source_features),
    }
    if len(bound_identities) != len(delivery_bindings or []):
        raise AcesGceCompositionError("ACES delivery bindings carry a duplicate resource identity")
    if source_identities - bound_identities:
        raise AcesGceCompositionError("a source-backed resource is missing its delivery binding")
    if bound_identities - source_identities:
        raise AcesGceCompositionError("a delivery binding does not match any deliverable resource")
