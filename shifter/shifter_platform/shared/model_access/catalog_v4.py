"""Immutable source revisions and provider-specific prices for mixed routing."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from shared.model_access.catalog_v3 import ModelAccessCatalogV3
from shared.model_access.core_models import ClosedModel, Digest, Identifier, PositiveInt, PriceSchedule, _require_unique


class SourceBinding(ClosedModel):
    """A shard's admitted source, credential version and price, never a secret."""

    shard_id: Identifier
    source_id: UUID | None
    source_revision: PositiveInt
    credential_revision: PositiveInt
    price_schedule_id: Identifier | None


class ModelAccessCatalogV4(ModelAccessCatalogV3):
    """v4 selects prices by actual source while preserving v1/v2/v3 digests."""

    contract_version: Literal["model-access-policy/v4"]  # type: ignore[assignment]
    source_bindings: Annotated[tuple[SourceBinding, ...], Field(min_length=1, max_length=256)]
    policy_catalog_digest: Digest | None = None
    policy_catalog: ModelAccessCatalogV3 | None = None

    @field_validator("source_bindings")
    @classmethod
    def normalize_source_bindings(cls, values: tuple[SourceBinding, ...]) -> tuple[SourceBinding, ...]:
        _require_unique(tuple(item.shard_id for item in values), "source_bindings.shard_id")
        return tuple(sorted(values, key=lambda item: item.shard_id))

    @model_validator(mode="after")
    def validate_source_bindings(self) -> Self:
        shards = {item.shard_id: item for item in self.shards}
        prices = {item.price_schedule_id: item for item in self.price_schedules}
        if set(shards) != {item.shard_id for item in self.source_bindings}:
            raise ValueError("every shard requires exactly one source binding")
        for binding in self.source_bindings:
            if binding.source_id is None:
                if binding.price_schedule_id is not None or shards[
                    binding.shard_id
                ].credential_ref.reference.startswith("source:"):
                    raise ValueError("legacy source bindings must retain alias pricing")
                continue
            expected = f"source:{binding.source_id}:{binding.source_revision}"
            if (
                binding.credential_revision != binding.source_revision
                or shards[binding.shard_id].credential_ref.reference != expected
                or shards[binding.shard_id].credential_ref.owner != "broker"
            ):
                raise ValueError("source credential must match its immutable binding")
            price = prices.get(binding.price_schedule_id) if binding.price_schedule_id is not None else None
            if price is None or not set(shards[binding.shard_id].billing_components).issubset(
                item.component for item in price.prices
            ):
                raise ValueError("source binding requires complete provider pricing")
            for alias in self.aliases:
                if (
                    binding.shard_id in alias.eligible_shard_ids
                    and price.currency != prices[alias.price_schedule_id].currency
                ):
                    raise ValueError("source pricing must use the alias currency")
        return self

    @model_validator(mode="after")
    def validate_policy_derivation(self) -> Self:
        """A source overlay cannot claim another policy's authorization digest."""
        from shared.model_access.digest import digest_matches

        base = self.policy_catalog
        if base is None:
            if self.policy_catalog_digest is not None:
                raise ValueError("policy identity requires its immutable catalog")
            return self
        if self.policy_catalog_digest != base.digest or not digest_matches(base, base.digest):
            raise ValueError("policy identity mismatch")
        mutable = {"digest", "contract_version", "aliases", "shards", "price_schedules", "quota_pools", "profiles"}
        for field in type(base).model_fields:
            if field not in mutable and getattr(self, field) != getattr(base, field):
                raise ValueError("source selection cannot change spending policy")
        profiles = {p.profile_id: p for p in self.profiles}
        if set(profiles) != {p.profile_id for p in base.profiles}:
            raise ValueError("source selection cannot add profiles")
        for old in base.profiles:
            current = profiles[old.profile_id]
            if (
                not set(current.capabilities).issubset(old.capabilities)
                or current.model_copy(update={"capabilities": old.capabilities}) != old
            ):
                raise ValueError("source selection may only narrow profile capabilities")
        self._validate_preserved_resources(base)
        return self

    def _validate_preserved_resources(self, base: ModelAccessCatalogV3) -> None:
        """Legacy resources remain unchanged; quota ceilings may only tighten."""
        for field, key in (("shards", "shard_id"), ("price_schedules", "price_schedule_id")):
            actual = {getattr(item, key): item for item in getattr(self, field)}
            if any(actual.get(getattr(item, key)) != item for item in getattr(base, field)):
                raise ValueError("source selection cannot alter incumbent resources")
        pools = {item.quota_pool_id: item for item in self.quota_pools}
        for old in base.quota_pools:
            current = pools.get(old.quota_pool_id)
            if current is None or current.limit > old.limit or current.model_copy(update={"limit": old.limit}) != old:
                raise ValueError("source selection cannot broaden incumbent quota")
        aliases = {item.logical_alias: item for item in self.aliases}
        if set(aliases) != {item.logical_alias for item in base.aliases}:
            raise ValueError("source selection cannot add logical aliases")
        for old_alias in base.aliases:
            current_alias = aliases[old_alias.logical_alias]
            restored = current_alias.model_copy(
                update={
                    field: getattr(old_alias, field)
                    for field in ("eligible_shard_ids", "strategy", "price_schedule_id")
                }
            )
            if restored != old_alias:
                raise ValueError("source selection cannot change alias authority")

    def price_for_alias(self, logical_alias: str, shard_id: str) -> PriceSchedule:
        """Require an eligible selected shard; never fall back to an alias price."""
        from shared.model_access.catalog import ContractError

        alias = next((item for item in self.aliases if item.logical_alias == logical_alias), None)
        if alias is None or shard_id not in alias.eligible_shard_ids:
            raise ContractError("request.alias_unavailable")
        binding = next(item for item in self.source_bindings if item.shard_id == shard_id)
        if binding.source_id is None:
            return super().price_for_alias(logical_alias, shard_id)
        return next(item for item in self.price_schedules if item.price_schedule_id == binding.price_schedule_id)
