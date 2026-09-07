"""Canonical immutable model-access policy DTOs (PLAT-202).

These models are the semantic contract shared by configuration, Engine, the
broker, and provider adapters.  They intentionally contain references rather
than credentials and remain independent of Django and provider SDKs.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")]
Capability = Annotated[str, Field(pattern=r"^[a-z][a-z0-9._-]{0,63}$")]
Region = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
SelectorIdentity = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9:/._@-]*$")]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class ClosedModel(BaseModel):
    """Frozen DTO base with an exact, non-extensible member set."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AllocationStrategy(StrEnum):
    FIXED_V1 = "fixed-v1"
    WEIGHTED_RENDEZVOUS_V1 = "weighted-rendezvous-v1"


class AssignmentAffinity(StrEnum):
    PER_RANGE = "per_range"
    PER_USER = "per_user"
    PER_POOL = "per_pool"


class Currency(StrEnum):
    AUD = "AUD"
    CAD = "CAD"
    CHF = "CHF"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    USD = "USD"


class BillingComponent(StrEnum):
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    CACHED_INPUT_TOKENS = "cached_input_tokens"
    CACHE_WRITE_TOKENS = "cache_write_tokens"
    REQUEST = "request"
    TOOL_CALL = "tool_call"
    IMAGE = "image"


class SelectorKind(StrEnum):
    SELECTED_RANGES = "selected_ranges"
    CTF_EVENT = "ctf_event"
    CTF_COHORT = "ctf_cohort"
    CTF_TEAM = "ctf_team"
    USER = "user"
    AUTH_GROUP = "auth_group"
    WORKSPACE = "workspace"
    ORGANIZATION = "organization"
    NAMED_COLLECTION = "named_collection"
    ALL_RANGES = "all_ranges"


class MembershipMode(StrEnum):
    SNAPSHOT = "snapshot"
    DYNAMIC = "dynamic"


class SharingFacet(StrEnum):
    PROFILE = "profile"
    PROVIDER_IDENTITY = "provider_identity"
    ROUTING = "routing"
    CAPACITY = "capacity"
    SPEND = "spend"
    RATE = "rate"
    CONCURRENCY = "concurrency"


def _require_unique(values: tuple[str | StrEnum, ...], field_name: str) -> None:
    rendered = [str(item) for item in values]
    if len(rendered) != len(set(rendered)):
        raise ValueError(f"{field_name} must not contain duplicate identities")


class AccessLimits(ClosedModel):
    """Finite request and deployment ceilings; omission never means unlimited."""

    max_request_seconds: PositiveInt
    max_request_bytes: PositiveInt
    max_input_tokens: PositiveInt
    max_output_tokens: PositiveInt
    max_requests_per_window: PositiveInt
    request_window_seconds: PositiveInt
    max_spend_micro_units: PositiveInt
    currency: Currency
    max_concurrent_requests: PositiveInt

    def tightened_with(self, other: AccessLimits) -> AccessLimits:
        if self.currency is not other.currency:
            raise ValueError("limits use different currencies")
        data = {
            name: min(getattr(self, name), getattr(other, name))
            for name in type(self).model_fields
            if name != "currency"
        }
        return AccessLimits(currency=self.currency, **data)


class OwnedReference(ClosedModel):
    """A typed owner-qualified reference, never embedded credential material."""

    owner: Identifier
    reference: Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9:/._@-]*$")]


class ComputeTargetReference(OwnedReference):
    """Range compute-placement identity, independent of model routing."""


class ModelProjectReference(OwnedReference):
    """Provider project that serves the model."""


class ModelAccountReference(OwnedReference):
    """Provider billing/account identity for model usage."""


class DynamicSecretProjectReference(OwnedReference):
    """Project that owns dynamic secret storage, never a model project alias."""


class BrokerWorkloadIdentityReference(OwnedReference):
    """Broker workload principal allowed to obtain provider invocation identity."""


class ProviderCredentialReference(OwnedReference):
    """Opaque broker-owned credential reference without credential material."""


class ModelProfile(ClosedModel):
    profile_id: Identifier
    capabilities: Annotated[tuple[Capability, ...], Field(min_length=1, max_length=64)]
    allowed_strategies: Annotated[tuple[AllocationStrategy, ...], Field(min_length=1, max_length=2)]
    data_regions: Annotated[tuple[Region, ...], Field(min_length=1, max_length=32)]
    limits: AccessLimits

    @field_validator("capabilities", "allowed_strategies", "data_regions")
    @classmethod
    def _normalize_sets(cls, values: tuple[object, ...]) -> tuple[object, ...]:
        return tuple(sorted(values, key=str))

    @model_validator(mode="after")
    def _sets_are_unique(self) -> ModelProfile:
        _require_unique(self.capabilities, "capabilities")
        _require_unique(self.allowed_strategies, "allowed_strategies")
        _require_unique(self.data_regions, "data_regions")
        if "global" in self.data_regions:
            raise ValueError("global is not an explicit data region")
        return self


class ScenarioNeed(ClosedModel):
    contract_version: Literal["model-access-scenario/v1"]
    scenario_digest: Digest
    workload_role: Identifier
    profile_id: Identifier
    required: StrictBool
    required_capabilities: Annotated[tuple[Capability, ...], Field(max_length=64)]
    allowed_capabilities: Annotated[tuple[Capability, ...], Field(min_length=1, max_length=64)]
    allowed_strategies: Annotated[tuple[AllocationStrategy, ...], Field(min_length=1, max_length=2)]
    data_regions: Annotated[tuple[Region, ...], Field(min_length=1, max_length=32)]
    limits: AccessLimits

    @field_validator(
        "required_capabilities",
        "allowed_capabilities",
        "allowed_strategies",
        "data_regions",
    )
    @classmethod
    def _normalize_sets(cls, values: tuple[object, ...]) -> tuple[object, ...]:
        return tuple(sorted(values, key=str))

    @model_validator(mode="after")
    def _validate_need(self) -> ScenarioNeed:
        for field_name in (
            "required_capabilities",
            "allowed_capabilities",
            "allowed_strategies",
            "data_regions",
        ):
            _require_unique(getattr(self, field_name), field_name)
        if not set(self.required_capabilities).issubset(self.allowed_capabilities):
            raise ValueError("required capabilities must be allowed")
        return self


class EffectiveProfile(ClosedModel):
    profile_id: Identifier
    required: StrictBool
    capabilities: tuple[Capability, ...]
    allowed_strategies: tuple[AllocationStrategy, ...]
    data_regions: tuple[Region, ...]
    limits: AccessLimits


class QuotaPool(ClosedModel):
    quota_pool_id: Identifier
    provider_adapter_id: Identifier
    provider_quota_identity: Annotated[str, Field(min_length=1, max_length=256)]
    dimension: Identifier
    unit: Annotated[str, Field(min_length=1, max_length=64)]
    limit: PositiveInt


class Price(ClosedModel):
    component: BillingComponent
    unit_denominator: PositiveInt
    price_micro_units: NonNegativeInt


class PriceSchedule(ClosedModel):
    price_schedule_id: Identifier
    currency: Currency
    valid_until: datetime
    prices: Annotated[tuple[Price, ...], Field(min_length=1, max_length=16)]

    @field_validator("prices")
    @classmethod
    def _normalize_prices(cls, values: tuple[Price, ...]) -> tuple[Price, ...]:
        return tuple(sorted(values, key=lambda item: item.component.value))

    @model_validator(mode="after")
    def _unique_components(self) -> PriceSchedule:
        _require_unique(tuple(price.component for price in self.prices), "prices.component")
        if self.valid_until.tzinfo is None:
            raise ValueError("valid_until must include a timezone")
        return self


class ModelShard(ClosedModel):
    shard_id: Identifier
    provider_adapter_id: Identifier
    compute_target_ref: ComputeTargetReference
    model_project_ref: ModelProjectReference
    model_account_ref: ModelAccountReference
    dynamic_secret_project_ref: DynamicSecretProjectReference
    broker_workload_identity_ref: BrokerWorkloadIdentityReference
    credential_ref: ProviderCredentialReference
    region: Region
    provider_model: Annotated[str, Field(min_length=1, max_length=256)]
    provider_model_version: Annotated[str, Field(min_length=1, max_length=64)]
    protocol: Annotated[str, Field(min_length=1, max_length=128)]
    capabilities: Annotated[tuple[Capability, ...], Field(min_length=1, max_length=64)]
    billing_components: Annotated[tuple[BillingComponent, ...], Field(min_length=1, max_length=16)]
    quota_pool_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=32)]
    weight: Annotated[StrictInt, Field(ge=1, le=64)]
    enabled: StrictBool

    @field_validator("capabilities", "billing_components", "quota_pool_ids")
    @classmethod
    def _normalize_sets(cls, values: tuple[object, ...]) -> tuple[object, ...]:
        return tuple(sorted(values, key=str))

    @model_validator(mode="after")
    def _unique_sets(self) -> ModelShard:
        _require_unique(self.capabilities, "capabilities")
        _require_unique(self.billing_components, "billing_components")
        _require_unique(self.quota_pool_ids, "quota_pool_ids")
        if self.region == "global" or self.provider_model_version == "latest":
            raise ValueError("shards require explicit region and model version")
        return self


class ModelAlias(ClosedModel):
    logical_alias: Identifier
    profile_id: Identifier
    strategy: AllocationStrategy
    affinity: AssignmentAffinity
    eligible_shard_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=32)]
    price_schedule_id: Identifier

    @field_validator("eligible_shard_ids")
    @classmethod
    def _normalize_shards(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(values))

    @model_validator(mode="after")
    def _unique_shards(self) -> ModelAlias:
        _require_unique(self.eligible_shard_ids, "eligible_shard_ids")
        if self.strategy is AllocationStrategy.FIXED_V1 and len(self.eligible_shard_ids) != 1:
            raise ValueError("fixed-v1 requires exactly one eligible shard")
        return self


class SharingSelector(ClosedModel):
    kind: SelectorKind
    ids: Annotated[tuple[SelectorIdentity, ...], Field(max_length=1000)] = ()
    members: Annotated[tuple[SharingSelector, ...], Field(max_length=32)] = ()
    include_spares: StrictBool = False

    @field_validator("ids")
    @classmethod
    def _normalize_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(values))

    @field_validator("members")
    @classmethod
    def _normalize_members(cls, values: tuple[SharingSelector, ...]) -> tuple[SharingSelector, ...]:
        return tuple(sorted(values, key=lambda item: (item.kind.value, item.ids, item.include_spares)))

    @model_validator(mode="after")
    def _closed_shape(self) -> SharingSelector:
        _require_unique(self.ids, "selector.ids")
        if self.kind is SelectorKind.ALL_RANGES:
            if self.ids or self.members or self.include_spares:
                raise ValueError("all_ranges has no ids, members, or spares flag")
        elif self.kind is SelectorKind.NAMED_COLLECTION:
            if self.ids or not self.members:
                raise ValueError("named_collection requires members and no ids")
            if any(member.kind is SelectorKind.NAMED_COLLECTION for member in self.members):
                raise ValueError("named collections cannot be recursively nested")
            member_keys = tuple((member.kind.value, member.ids, member.include_spares) for member in self.members)
            if len(member_keys) != len(set(member_keys)):
                raise ValueError("named collection members must be unique")
        elif self.members or not self.ids:
            raise ValueError("atomic selectors require ids and no members")
        return self


class AliasAffinity(ClosedModel):
    logical_alias: Identifier
    affinity: AssignmentAffinity


class SharingPool(ClosedModel):
    sharing_pool_id: Identifier
    routing_revision: PositiveInt
    alias_affinities: Annotated[tuple[AliasAffinity, ...], Field(max_length=64)] = ()
    provider_pool_ref: Identifier | None = None
    capacity_account_ref: Identifier | None = None
    spend_account_refs: Annotated[tuple[Identifier, ...], Field(max_length=64)] = ()
    rate_account_refs: Annotated[tuple[Identifier, ...], Field(max_length=64)] = ()
    concurrency_account_refs: Annotated[tuple[Identifier, ...], Field(max_length=64)] = ()

    @field_validator("alias_affinities")
    @classmethod
    def _normalize_aliases(cls, values: tuple[AliasAffinity, ...]) -> tuple[AliasAffinity, ...]:
        return tuple(sorted(values, key=lambda item: item.logical_alias))

    @field_validator("spend_account_refs", "rate_account_refs", "concurrency_account_refs")
    @classmethod
    def _normalize_accounts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(values))

    @model_validator(mode="after")
    def _validate_pool(self) -> SharingPool:
        aliases = tuple(item.logical_alias for item in self.alias_affinities)
        _require_unique(aliases, "alias_affinities.logical_alias")
        for field_name in ("spend_account_refs", "rate_account_refs", "concurrency_account_refs"):
            _require_unique(getattr(self, field_name), field_name)
        if not any(
            (
                self.alias_affinities,
                self.provider_pool_ref,
                self.capacity_account_ref,
                self.spend_account_refs,
                self.rate_account_refs,
                self.concurrency_account_refs,
            )
        ):
            raise ValueError("sharing pool must share at least one facet")
        return self


class SharingBinding(ClosedModel):
    contract_version: Literal["model-access-sharing/v1"]
    sharing_binding_id: Identifier
    deployment_id: UUID
    selector: SharingSelector
    membership_mode: MembershipMode
    membership_revision: PositiveInt
    authorized_publisher_ref: OwnedReference
    profile_id: Identifier | None = None
    sharing_pool_id: Identifier
    facets: Annotated[tuple[SharingFacet, ...], Field(min_length=1, max_length=7)]
    priority: Annotated[StrictInt, Field(ge=0, le=1000)] = 0
    effective_from: datetime
    effective_until: datetime
    definition_digest: Digest

    @field_validator("facets")
    @classmethod
    def _normalize_facets(cls, values: tuple[SharingFacet, ...]) -> tuple[SharingFacet, ...]:
        return tuple(sorted(values, key=str))

    @model_validator(mode="after")
    def _validate_binding(self) -> SharingBinding:
        _require_unique(self.facets, "facets")
        if self.effective_from.tzinfo is None or self.effective_until.tzinfo is None:
            raise ValueError("effective interval must include timezones")
        if self.effective_until <= self.effective_from:
            raise ValueError("effective_until must be after effective_from")
        if SharingFacet.PROFILE in self.facets and self.profile_id is None:
            raise ValueError("profile sharing requires profile_id")
        return self


class ModelAccessCatalog(ClosedModel):
    contract_version: Literal["model-access-policy/v1"]
    deployment_id: UUID
    enabled: StrictBool
    profiles: Annotated[tuple[ModelProfile, ...], Field(min_length=1, max_length=64)]
    quota_pools: Annotated[tuple[QuotaPool, ...], Field(min_length=1, max_length=256)]
    price_schedules: Annotated[tuple[PriceSchedule, ...], Field(min_length=1, max_length=128)]
    shards: Annotated[tuple[ModelShard, ...], Field(min_length=1, max_length=256)]
    aliases: Annotated[tuple[ModelAlias, ...], Field(min_length=1, max_length=128)]
    sharing_pools: Annotated[tuple[SharingPool, ...], Field(max_length=128)] = ()
    sharing_bindings: Annotated[tuple[SharingBinding, ...], Field(max_length=256)] = ()
    digest: Digest

    @field_validator("profiles")
    @classmethod
    def _normalize_profiles(cls, values: tuple[ModelProfile, ...]) -> tuple[ModelProfile, ...]:
        return tuple(sorted(values, key=lambda item: item.profile_id))

    @field_validator("quota_pools")
    @classmethod
    def _normalize_quota_pools(cls, values: tuple[QuotaPool, ...]) -> tuple[QuotaPool, ...]:
        return tuple(sorted(values, key=lambda item: item.quota_pool_id))

    @field_validator("price_schedules")
    @classmethod
    def _normalize_prices(cls, values: tuple[PriceSchedule, ...]) -> tuple[PriceSchedule, ...]:
        return tuple(sorted(values, key=lambda item: item.price_schedule_id))

    @field_validator("shards")
    @classmethod
    def _normalize_shards(cls, values: tuple[ModelShard, ...]) -> tuple[ModelShard, ...]:
        return tuple(sorted(values, key=lambda item: item.shard_id))

    @field_validator("aliases")
    @classmethod
    def _normalize_aliases(cls, values: tuple[ModelAlias, ...]) -> tuple[ModelAlias, ...]:
        return tuple(sorted(values, key=lambda item: item.logical_alias))

    @field_validator("sharing_pools")
    @classmethod
    def _normalize_sharing_pools(cls, values: tuple[SharingPool, ...]) -> tuple[SharingPool, ...]:
        return tuple(sorted(values, key=lambda item: item.sharing_pool_id))

    @field_validator("sharing_bindings")
    @classmethod
    def _normalize_bindings(cls, values: tuple[SharingBinding, ...]) -> tuple[SharingBinding, ...]:
        return tuple(sorted(values, key=lambda item: item.sharing_binding_id))

    @model_validator(mode="after")
    def _validate_references(self) -> ModelAccessCatalog:
        ids = _catalog_ids(self)
        _validate_quota_references(self, ids)
        _validate_alias_references(self, ids)
        _validate_sharing_references(self, ids)
        return self


def _catalog_ids(catalog: ModelAccessCatalog) -> dict[str, set[str]]:
    collections = {
        "profiles": (catalog.profiles, "profile_id"),
        "quota_pools": (catalog.quota_pools, "quota_pool_id"),
        "price_schedules": (catalog.price_schedules, "price_schedule_id"),
        "shards": (catalog.shards, "shard_id"),
        "aliases": (catalog.aliases, "logical_alias"),
        "sharing_pools": (catalog.sharing_pools, "sharing_pool_id"),
        "sharing_bindings": (catalog.sharing_bindings, "sharing_binding_id"),
    }
    ids: dict[str, set[str]] = {}
    for name, (items, field_name) in collections.items():
        values = tuple(str(getattr(item, field_name)) for item in items)
        _require_unique(values, field_name)
        ids[name] = set(values)
    return ids


def _validate_quota_references(catalog: ModelAccessCatalog, ids: dict[str, set[str]]) -> None:
    real_quotas = tuple(
        (pool.provider_adapter_id, pool.provider_quota_identity, pool.dimension, pool.unit)
        for pool in catalog.quota_pools
    )
    if len(real_quotas) != len(set(real_quotas)):
        raise ValueError("one real provider quota identity must map to one quota pool")
    pools = {pool.quota_pool_id: pool for pool in catalog.quota_pools}
    for shard in catalog.shards:
        if not set(shard.quota_pool_ids).issubset(ids["quota_pools"]):
            raise ValueError("shard references an unknown quota pool")
        if any(pools[pool_id].provider_adapter_id != shard.provider_adapter_id for pool_id in shard.quota_pool_ids):
            raise ValueError("shard and quota pool adapter identities differ")


def _validate_alias_references(catalog: ModelAccessCatalog, ids: dict[str, set[str]]) -> None:
    shards = {item.shard_id: item for item in catalog.shards}
    prices = {item.price_schedule_id: item for item in catalog.price_schedules}
    profiles = {item.profile_id: item for item in catalog.profiles}
    for alias in catalog.aliases:
        if alias.profile_id not in profiles or alias.price_schedule_id not in prices:
            raise ValueError("alias references an unknown profile or price schedule")
        if not set(alias.eligible_shard_ids).issubset(ids["shards"]):
            raise ValueError("alias references an unknown shard")
        profile = profiles[alias.profile_id]
        if alias.strategy not in profile.allowed_strategies:
            raise ValueError("alias strategy is not allowed by its profile")
        priced_components = {item.component for item in prices[alias.price_schedule_id].prices}
        for shard_id in alias.eligible_shard_ids:
            shard = shards[shard_id]
            if not set(profile.capabilities).issubset(shard.capabilities):
                raise ValueError("eligible shard cannot satisfy its profile")
            if not set(shard.billing_components).issubset(priced_components):
                raise ValueError("eligible shard has an unpriced billing component")
            if shard.region not in profile.data_regions:
                raise ValueError("eligible shard violates profile data regions")


def _validate_sharing_references(catalog: ModelAccessCatalog, ids: dict[str, set[str]]) -> None:
    for pool in catalog.sharing_pools:
        if any(item.logical_alias not in ids["aliases"] for item in pool.alias_affinities):
            raise ValueError("sharing pool references an unknown alias")
    for binding in catalog.sharing_bindings:
        if binding.deployment_id != catalog.deployment_id or binding.sharing_pool_id not in ids["sharing_pools"]:
            raise ValueError("sharing binding references another deployment or unknown pool")
        if binding.profile_id is not None and binding.profile_id not in ids["profiles"]:
            raise ValueError("sharing binding references an unknown profile")


class AccessGrant(ClosedModel):
    contract_version: Literal["model-access-grant/v1"]
    deployment_id: UUID
    range_id: UUID
    execution_generation_id: UUID
    subject_ref: OwnedReference
    policy_digest: Digest
    grant_epoch: PositiveInt
    limits: AccessLimits
    deadline: datetime
    alias_shards: Annotated[dict[Identifier, Identifier], Field(max_length=128)]

    @field_validator("deadline")
    @classmethod
    def _deadline_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("deadline must include a timezone")
        return value
