"""Immutable deployment bindings for an independently installed runtime plugin.

These are Shifter execution decisions beside the RAES plan, not authoring intent
inside that plan. A retained pin remains usable for cleanup after registry edits.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self
from uuid import UUID, uuid5

from pydantic import Field, model_validator
from shifter_adapter_sdk.runtime import (
    PROTOCOL,
    ClosedModel,
    Digest,
    GuestTarget,
    Identifier,
    PluginManifest,
    RuntimeInput,
    canonical_digest,
)


class PluginTargetBindings(ClosedModel):
    """The logical guest names requested by an installed plugin and exact nodes."""

    targets: dict[Identifier, Annotated[str, Field(min_length=1, max_length=1024)]] = Field(
        min_length=1,
        max_length=64,
    )
    parameters: dict[Identifier, Annotated[str, Field(max_length=8192)]] = Field(
        default_factory=dict,
        max_length=64,
        repr=False,
    )

    def validate_manifest(self, manifest: PluginManifest) -> None:
        if set(self.targets) != set(manifest.required_bindings):
            raise ValueError("Plugin target bindings do not match its declaration")
        if set(self.parameters) != set(manifest.required_parameters):
            raise ValueError("Plugin parameters do not match its declaration")

    def validate_plan(self, plan: object) -> None:
        resources = plan.get("resources") if isinstance(plan, dict) else None
        if not isinstance(resources, dict):
            raise ValueError("Plugin binding requires a compiled resource plan")
        for address in self.targets.values():
            entry = resources.get(address)
            if not isinstance(entry, dict) or entry.get("resource_type") != "node":
                raise ValueError("Plugin binding references a guest absent from the compiled plan")
            payload = entry.get("payload")
            if not isinstance(payload, dict) or (
                payload.get("count") is not None and (type(payload["count"]) is not int or payload["count"] != 1)
            ):
                raise ValueError("A plugin binding requires exactly one guest")


class RuntimePluginScope(ClosedModel):
    """Authorized tenancy and verified pack identity carried beside the plan."""

    organization_uuid: UUID
    pack_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    pack_digest: Digest


class RuntimePluginPin(RuntimePluginScope):
    """Exact executable and pack binding admitted for one range's lifetime."""

    binding_version: Literal[1] = 1
    installation_id: UUID
    manifest: PluginManifest
    bindings: PluginTargetBindings

    @model_validator(mode="after")
    def validate_pin(self) -> Self:
        self.bindings.validate_manifest(self.manifest)
        if len(self.model_dump_json().encode("utf-8")) > 32_768:
            raise ValueError("Plugin binding exceeds its input budget")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


def runtime_plugin_requests(
    pin: RuntimePluginPin,
    plan: dict,
    operation_id: UUID,
    range_id: int,
    *,
    backend: str = "gce",
) -> tuple[RuntimeInput, ...]:
    """Plan guest actions before provisioning, with no realized addresses or secrets.

    Runtime hosts support RAES GCE and EC2 guest configuration. Plans are pure:
    the isolated worker cannot observe guests or call a provider. The host later
    resolves actions against its own realized node-to-transport map.
    """
    pin = RuntimePluginPin.model_validate(pin)
    providers: dict[str, Literal["aws", "gcp"]] = {"gce": "gcp", "ec2": "aws"}
    if backend not in providers:
        raise ValueError("Runtime plugins are not supported by this range backend")
    pin.bindings.validate_plan(plan)
    targets = {
        name: GuestTarget(node_address=address, os_family=plan["resources"][address]["payload"]["os_family"])
        for name, address in pin.bindings.targets.items()
    }
    return tuple(
        RuntimeInput(
            protocol=PROTOCOL,
            invocation_id=uuid5(operation_id, f"runtime-plugin:{phase}"),
            operation_id=operation_id,
            pack_digest=pin.pack_digest,
            manifest=pin.manifest,
            phase=phase,
            provider=providers[backend],
            range_id=range_id,
            targets=targets,
            parameters=pin.bindings.parameters,
        )
        for phase in ("validate", "configure", "verify")
    )
