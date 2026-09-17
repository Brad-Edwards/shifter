"""Non-secret broker execution configuration, separate from infrastructure state."""

import json

from pydantic import BaseModel, ConfigDict
from shared.model_access import load_catalog_json
from shared.model_access.core_models import Identifier
from shared.model_access.provider_runtime import ProviderInventory

from .gcp_model_broker import ResourceName


class ModelBrokerRuntimeSettings(BaseModel):
    """Mounted provider bindings and versioned Kubernetes Secret references only."""

    model_config = ConfigDict(extra="forbid")
    provider_inventory: ProviderInventory
    fingerprint_secret_name: ResourceName
    fingerprint_key_version: Identifier
    fingerprint_previous_secret_name: ResourceName = ""


def project_broker_runtime(value, *, catalog_json, provider, model_identities=None):
    """Reject incomplete execution bindings before Helm or cloud deployment."""
    settings = ModelBrokerRuntimeSettings.model_validate(value)
    if not settings.fingerprint_secret_name:
        raise ValueError("model broker requires a versioned fingerprint Secret")
    catalog = load_catalog_json(catalog_json)
    if catalog.contract_version != "model-access-policy/v3" or not catalog.enabled:
        raise ValueError("active model broker requires an enabled v3 accounting catalog")
    shards = {shard.shard_id: shard for shard in catalog.shards if shard.enabled}
    targets = {target.shard_id: target for target in settings.provider_inventory.targets}
    if set(shards) != set(targets):
        raise ValueError("provider inventory must match the enabled catalog shards")
    for name, target in targets.items():
        target.bind(shards[name])
        if target.provider != ("vertex-v1" if provider == "gcp" else "bedrock-v1"):
            raise ValueError("provider inventory requires a supported workload identity on this deployment")
        if provider == "gcp" and (model_identities or {}).get(target.project) != target.principal:
            raise ValueError("provider target differs from the applied invocation identity")
    schedules = {schedule.price_schedule_id: schedule for schedule in catalog.price_schedules}
    for alias in catalog.aliases:
        prices = {price.component: price.price_micro_units for price in schedules[alias.price_schedule_id].prices}
        if not {"input_tokens", "output_tokens", "request"}.issubset(prices) or prices["request"] != 0:
            raise ValueError("Messages delivery needs input/output prices and free token-count request pricing")
    result = settings.model_dump(mode="json", exclude={"provider_inventory"})
    result["providers_json"] = json.dumps(
        settings.provider_inventory.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return result
