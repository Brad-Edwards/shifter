"""Resolve unprojected feature sources to recipe-acquired artifacts at launch (ADR-034-R11, R12).

A scenario declares third-party software as an RAES ``artifact`` feature by name
and version only; no pack carries its bytes. At materialization this resolver
asks Engine for the backend-owned artifact (acquiring it once if needed, waiting
a bounded time) and turns it into the same byte-free feature delivery binding a
pack-projected artifact produces. Anything it cannot satisfy fails this range's
materialization; other ranges are unaffected.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from django.conf import settings

from shared.raes.content_delivery import FEATURE_BINDING_VERSION, ContentDeliveryError, DeliveryBinding

# Guest platform of the backend's Linux range instances (x86_64, glibc). A backend
# realization fact, not authored intent: other node OS families have no platform
# with acquisition support yet and fail closed.
_GUEST_PLATFORMS = {"linux": "linux-x64-glibc"}
_ACQUIRABLE_MECHANISMS = {"exact-artifact", "backend-owned-artifact"}


def _resources(plan: Mapping[str, Any]) -> Mapping[str, Any]:
    resources = plan.get("resources")
    return resources if isinstance(resources, Mapping) else {}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _payload(resource: object) -> Mapping[str, Any]:
    return _mapping(_mapping(resource).get("payload"))


def _guest_platform(plan: Mapping[str, Any], node_address: str) -> str:
    """Platform of the node a feature binds to, from its compiled OS family."""
    payload = _payload(_resources(plan).get(node_address))
    node = _mapping(_mapping(payload.get("spec")).get("node"))
    os_family = str(payload.get("os_family") or node.get("os") or "").lower()
    platform = _GUEST_PLATFORMS.get(os_family)
    if platform is None:
        raise ContentDeliveryError(f"feature artifacts are not acquirable for '{os_family or 'unknown'}' nodes")
    return platform


def _check_requirement(plan: Mapping[str, Any], resource_address: str, source_name: str) -> None:
    """Honor the authored RAES artifact requirement on the feature's source."""
    template = _mapping(_mapping(_payload(_resources(plan).get(resource_address)).get("spec")).get("template"))
    requirement = _mapping(template.get("source")).get("artifact_requirement")
    if not isinstance(requirement, Mapping):
        return
    if requirement.get("explicitness") == "constrained":
        raise ContentDeliveryError(f"feature '{source_name}': unsupported constrained realization")
    routes = requirement.get("permitted_routes") or []
    permitted = any(
        isinstance(route, Mapping)
        and isinstance(route.get("mechanism"), Mapping)
        and route["mechanism"].get("mechanism") in _ACQUIRABLE_MECHANISMS
        and route.get("acquisition") == "pull"
        for route in routes
    )
    if not permitted:
        raise ContentDeliveryError(
            f"feature '{source_name}': unsupported backend mechanism (acquisition not permitted)"
        )


def feature_artifact_resolver(
    plan: Mapping[str, Any], *, target: Any | None = None
) -> Callable[[Any], DeliveryBinding]:
    """Return the ``acquire_feature`` resolver for one compiled plan.

    ``target`` (the delivery storage location) defaults to the deployment's.
    """

    def acquire(ref: Any) -> DeliveryBinding:
        from engine.services import (
            ArtifactRequest,
            FeatureArtifactUnavailableError,
            await_ready,
            default_storage_target,
        )

        if ref.feature_type != "artifact":
            raise ContentDeliveryError(f"feature '{ref.source_name}': only artifact features are acquirable")
        _check_requirement(plan, ref.address, ref.source_name)
        request = ArtifactRequest(ref.source_name, ref.source_version, _guest_platform(plan, ref.target_address))
        try:
            row = await_ready(
                request,
                target=target or default_storage_target(),
                timeout=timedelta(seconds=settings.RAES_FEATURE_ARTIFACT_WAIT_SECONDS),
            )
        except FeatureArtifactUnavailableError as exc:
            raise ContentDeliveryError(f"feature artifact '{ref.source_name}' is unavailable: {exc}") from None
        return DeliveryBinding(
            content_address=None,
            sha256=row.sha256,
            storage_key=row.storage_key,
            byte_count=row.byte_count,
            binding_version=FEATURE_BINDING_VERSION,
            resource_type="feature-binding",
            resource_address=ref.address,
            payload_kind=row.payload_kind,
            install_policy=row.install_policy,
        )

    return acquire
