"""Activation rejects incomplete provider and accounting configuration."""

import json
from pathlib import Path

import pytest
from shared.model_access import seal_catalog

from installation.model_broker_runtime import project_broker_runtime


@pytest.fixture
def active_runtime():
    catalog = json.loads(
        (Path(__file__).resolve().parents[3] / "docs/architecture/model-access/example-policy.v3.json").read_text()
    )
    catalog.pop("digest")
    catalog["enabled"] = True
    target = {
        "shard_id": "vertex-primary",
        "provider": "vertex-v1",
        "region": "europe-west4",
        "count_region": "eu",
        "model": "publishers/anthropic/models/claude-sonnet",
        "credential_reference": "impersonate:gsa/model-invoke",
        "principal": "model-invoke@models-example.iam.gserviceaccount.com",
        "project": "models-example",
        "context_window_tokens": 200000,
    }
    settings = {
        "provider_inventory": {"contract_version": "model-broker-providers/v1", "targets": [target]},
        "fingerprint_secret_name": "broker-fingerprint-v1",
        "fingerprint_key_version": "v1",
    }
    return settings, catalog


def project(settings, catalog, **overrides):
    return project_broker_runtime(
        settings,
        catalog_json=seal_catalog(catalog).model_dump_json(),
        provider=overrides.get("provider", "gcp"),
        model_identities=overrides.get(
            "model_identities", {"models-example": "model-invoke@models-example.iam.gserviceaccount.com"}
        ),
    )


def test_projects_nonsecret_inventory_and_versioned_secret_reference(active_runtime):
    settings, catalog = active_runtime
    projected = project(settings, catalog)
    assert json.loads(projected["providers_json"]) == settings["provider_inventory"]
    assert projected["fingerprint_secret_name"] == "broker-fingerprint-v1"


# Invalid synthetic key envelope: decoded content is just "abc", not key material.
_KEY_LABEL = "PRIVATE KEY"


@pytest.mark.parametrize("material", [f"-----BEGIN {_KEY_LABEL}-----\nYWJj\n-----END {_KEY_LABEL}-----", "not a CA"])
def test_guest_trust_refuses_nonpublic_certificate_material(active_runtime, material):
    settings, catalog = active_runtime
    settings["guest_trust_ca_pem"] = material
    with pytest.raises(ValueError):
        project(settings, catalog)


@pytest.mark.parametrize("fault", ["identity", "shard", "model", "key", "count_price", "disabled", "cloud"])
def test_rejects_execution_config_that_cannot_enforce_catalog(active_runtime, fault):
    settings, catalog = active_runtime
    overrides = {}
    if fault == "identity":
        overrides["model_identities"] = {"models-example": "different@models-example.iam.gserviceaccount.com"}
    elif fault in {"shard", "model"}:
        settings["provider_inventory"]["targets"][0]["shard_id" if fault == "shard" else "model"] = (
            "unbound-shard" if fault == "shard" else "publishers/anthropic/models/different-model"
        )
    elif fault == "key":
        settings["fingerprint_secret_name"] = ""
    elif fault == "count_price":
        catalog["price_schedules"][0]["prices"][-1]["price_micro_units"] = 1
    elif fault == "disabled":
        catalog["enabled"] = False
    else:
        overrides["provider"] = "aws"
    with pytest.raises(ValueError):
        project(settings, catalog, **overrides)
