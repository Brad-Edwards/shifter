"""Variant configuration for post-deploy range smoke tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VariantName = Literal["linux", "windows"]


@dataclass(frozen=True)
class SmokeVariant:
    name: VariantName
    scenario_id: str
    required_agent_keys: tuple[str, ...]
    primary_protocol: Literal["ssh", "rdp"]
    provision_timeout_seconds: int
    connectivity_timeout_seconds: int


VARIANTS: dict[VariantName, SmokeVariant] = {
    "linux": SmokeVariant(
        name="linux",
        scenario_id="basic",
        required_agent_keys=(),
        primary_protocol="ssh",
        provision_timeout_seconds=1800,
        connectivity_timeout_seconds=600,
    ),
    "windows": SmokeVariant(
        name="windows",
        scenario_id="ad_attack_lab",
        required_agent_keys=("windows", "linux"),
        primary_protocol="rdp",
        provision_timeout_seconds=3600,
        connectivity_timeout_seconds=900,
    ),
}


def parse_variant(raw: str) -> SmokeVariant:
    key = raw.strip().lower()
    if key not in VARIANTS:
        allowed = ", ".join(sorted(VARIANTS))
        msg = f"unknown smoke variant {raw!r}; expected one of: {allowed}"
        raise ValueError(msg)
    return VARIANTS[key]
