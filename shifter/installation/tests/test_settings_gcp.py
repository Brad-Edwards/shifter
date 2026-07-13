"""Tests for the closed GCP backend settings model (``installation.settings_gcp``)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from installation.range_egress import RangeEgressMode, RangeEgressPolicy
from installation.settings_gcp import GcpBackendSettings


class TestGcpBackendSettings:
    def test_minimal_valid_settings_defaults_range_egress_to_status_quo(self):
        settings = GcpBackendSettings.model_validate({"project_id": "acme-shifter", "region": "us-central1"})
        assert settings.project_id == "acme-shifter"
        assert settings.region == "us-central1"
        # range_egress is composed from the canonical model, not copied.
        assert isinstance(settings.range_egress, RangeEgressPolicy)
        assert settings.range_egress.mode is RangeEgressMode.STATUS_QUO
        assert settings.range_egress.allowed_cidrs == []

    def test_model_is_closed_and_rejects_unknown_settings(self):
        # extra='forbid' — an unknown GCP setting fails before any infrastructure mutation.
        with pytest.raises(ValidationError):
            GcpBackendSettings.model_validate({"project_id": "acme-shifter", "region": "us-central1", "bogus": "value"})

    def test_project_id_is_required(self):
        with pytest.raises(ValidationError):
            GcpBackendSettings.model_validate({"region": "us-central1"})

    def test_region_is_required(self):
        with pytest.raises(ValidationError):
            GcpBackendSettings.model_validate({"project_id": "acme-shifter"})

    @pytest.mark.parametrize(
        "project_id",
        [
            "acme",  # too short (< 6)
            "ACME-shifter",  # uppercase
            "acme_shifter",  # underscore
            "1acme-shifter",  # leading digit
            "acme-shifter-",  # trailing hyphen
            "a" * 31,  # too long (> 30)
        ],
    )
    def test_invalid_project_id_is_rejected(self, project_id):
        with pytest.raises(ValidationError):
            GcpBackendSettings.model_validate({"project_id": project_id, "region": "us-central1"})

    @pytest.mark.parametrize("project_id", ["acme-shifter", "your-gcp-project", "shifter", "abc123-def"])
    def test_valid_project_id_is_accepted(self, project_id):
        settings = GcpBackendSettings.model_validate({"project_id": project_id, "region": "us-central1"})
        assert settings.project_id == project_id

    @pytest.mark.parametrize("region", ["", "US-Central1", "us central1", "-us-central1"])
    def test_invalid_region_is_rejected(self, region):
        with pytest.raises(ValidationError):
            GcpBackendSettings.model_validate({"project_id": "acme-shifter", "region": region})

    @pytest.mark.parametrize("region", ["us-central1", "europe-west4", "asia-northeast1"])
    def test_valid_region_is_accepted(self, region):
        settings = GcpBackendSettings.model_validate({"project_id": "acme-shifter", "region": region})
        assert settings.region == region

    def test_range_egress_is_composed_and_validated(self):
        settings = GcpBackendSettings.model_validate(
            {
                "project_id": "acme-shifter",
                "region": "us-central1",
                "range_egress": {"mode": "allowlist", "allowed_cidrs": ["203.0.113.0/24"]},
            }
        )
        assert settings.range_egress.mode is RangeEgressMode.ALLOWLIST
        assert settings.range_egress.allowed_cidrs == ["203.0.113.0/24"]

    def test_range_egress_bad_cidr_is_rejected_via_composed_policy(self):
        # The GCP model reuses RangeEgressPolicy's validators rather than copying them.
        with pytest.raises(ValidationError):
            GcpBackendSettings.model_validate(
                {
                    "project_id": "acme-shifter",
                    "region": "us-central1",
                    "range_egress": {"mode": "allowlist", "allowed_cidrs": ["not-a-cidr"]},
                }
            )


class TestGcpBundleIntegration:
    """The gcp registry entry uses the closed model, and the loader enforces it end to end."""

    def test_registry_gcp_bundle_uses_the_closed_settings_model(self):
        from installation.registry import get_backend_bundle

        assert get_backend_bundle("gcp").settings_model is GcpBackendSettings

    def _gcp_config(self, settings: dict) -> dict:
        return {
            "backend": "gcp",
            "deployment": {"name": "shifter", "domain": "shifter.example.com"},
            "secrets": {"django_secret_key": "prompt"},
            "settings": settings,
        }

    def test_loader_accepts_valid_gcp_settings_and_normalizes_range_egress(self, write_config):
        from installation.loader import load_root_config

        cfg = load_root_config(write_config(self._gcp_config({"project_id": "acme-shifter", "region": "us-central1"})))
        assert cfg.settings["project_id"] == "acme-shifter"
        # range_egress is normalized to the canonical status-quo shape (json mode) by the loader.
        assert cfg.settings["range_egress"] == {"mode": "status-quo", "allowed_cidrs": []}

    def test_loader_rejects_unknown_gcp_setting_fail_closed(self, write_config):
        from installation.errors import InstallationConfigError
        from installation.loader import load_root_config

        with pytest.raises(InstallationConfigError) as excinfo:
            load_root_config(
                write_config(self._gcp_config({"project_id": "acme-shifter", "region": "us-central1", "bogus": "x"}))
            )
        assert any(issue.path == "settings.bogus" for issue in excinfo.value.issues)

    def test_loader_rejects_gcp_settings_missing_project_id(self, write_config):
        from installation.loader import load_root_config, validate_root_config_file

        issues = validate_root_config_file(write_config(self._gcp_config({"region": "us-central1"})))
        assert any(issue.path == "settings.project_id" for issue in issues)
        # load_root_config raises for the same config.
        from installation.errors import InstallationConfigError

        with pytest.raises(InstallationConfigError):
            load_root_config(write_config(self._gcp_config({"region": "us-central1"})))
