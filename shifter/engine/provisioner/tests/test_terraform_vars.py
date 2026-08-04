"""Tests for terraform_vars provider dispatch (unsupported-backend fail-closed).

Covers the two genuine gcp-vs-aws dispatch sites in terraform_vars.py that
previously fell through to AWS-only behavior for any non-gcp provider value:
``_resolve_instance_type`` (EC2 instance-type resolution) and
``_build_range_terraform_variables`` (AWS-only Terraform variable set).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class TestResolveInstanceTypeProviderDispatch:
    """``_resolve_instance_type`` must fail closed for an unsupported provider."""

    def test_raises_for_unsupported_provider(self, monkeypatch):
        import terraform_vars
        from cloud.exceptions import CloudProviderNotImplementedError

        # resolve_cloud_provider() only ever returns a KNOWN_BACKENDS value or
        # raises, so a future third backend is exercised via the module's own
        # imported resolver reference (ADR-019 boundary-mock-policy: object-form
        # monkeypatch.setattr on the first-party module, not a string patch target).
        monkeypatch.setattr(terraform_vars, "resolve_cloud_provider", lambda *a, **k: "azure")

        with pytest.raises(CloudProviderNotImplementedError, match="azure"):
            terraform_vars._resolve_instance_type("attacker", "kali", None)

    def test_override_wins_before_provider_is_resolved(self, monkeypatch):
        """An explicit override must short-circuit before the provider check."""
        import terraform_vars

        monkeypatch.setattr(terraform_vars, "resolve_cloud_provider", lambda *a, **k: "azure")

        assert terraform_vars._resolve_instance_type("attacker", "kali", "custom.type") == "custom.type"


class TestBuildRangeTerraformVariablesProviderDispatch:
    """``_build_range_terraform_variables`` must fail closed for an unsupported provider."""

    def test_raises_for_unsupported_provider(self, monkeypatch):
        import terraform_vars
        from cloud.exceptions import CloudProviderNotImplementedError

        monkeypatch.setenv("CLOUD_PROVIDER", "aws")
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("RANGE_INSTANCE_PROFILE_NAME", "shifter-dev-range-profile")
        monkeypatch.setattr(
            terraform_vars,
            "load_range_network_config",
            lambda: SimpleNamespace(
                network_id="vpc-test",
                network_cidr="10.1.0.0/16",
                primary_portal_cidr="10.0.0.0/16",
            ),
        )
        monkeypatch.setattr(terraform_vars, "get_range_availability_zone", lambda: "us-east-2a")
        # The provider check at the end of _build_range_terraform_variables reads
        # state_helpers._get_cloud_provider (imported into terraform_vars); patch
        # that reference so no subnets/instances need to exist to reach it.
        monkeypatch.setattr(terraform_vars, "_get_cloud_provider", lambda: "azure")

        with pytest.raises(CloudProviderNotImplementedError, match="azure"):
            terraform_vars._build_range_terraform_variables(
                request_id="req-1",
                range_id=1,
                user_id=2,
                range_spec={"ngfw": False, "subnets": []},
            )


class TestBuildTfInstanceSftpRoot:
    """The AWS SFTP root is operator image metadata, never scenario input (#375)."""

    def test_base_ami_keeps_the_reference_root_per_os(self):
        import terraform_vars

        assert (
            terraform_vars._build_tf_instance({"os_type": "kali", "role": "attacker"})["sftp_root_directory"]
            == "/home/kali"
        )
        assert (
            terraform_vars._build_tf_instance({"os_type": "ubuntu", "role": "victim"})["sftp_root_directory"]
            == "/home/ubuntu"
        )
        # DC keeps the Windows Administrator root.
        assert (
            terraform_vars._build_tf_instance({"os_type": "ubuntu", "role": "dc"})["sftp_root_directory"]
            == "/C:/Users/Administrator/Downloads"
        )

    def test_scenario_supplied_root_is_ignored(self):
        """Tenant/scenario input must not widen the guest SFTP root (security)."""
        import terraform_vars

        built = terraform_vars._build_tf_instance({"os_type": "kali", "role": "attacker", "sftp_root_directory": "/"})
        assert built["sftp_root_directory"] == "/home/kali"

    def test_ami_key_override_fails_closed_with_no_root(self):
        """An overridden AMI has no operator root source, so it retains no root.

        Exercised at the resolver (an ``ami_key`` in ``_build_tf_instance`` would
        also trigger a live SSM AMI lookup, which is out of scope here).
        """
        import terraform_vars

        inst = {"os_type": "kali", "role": "attacker", "ami_key": "custom-kali"}
        assert terraform_vars._resolve_instance_sftp_root(inst, "kali", "attacker") == ""

    def test_unknown_os_has_empty_root(self):
        import terraform_vars

        assert (
            terraform_vars._build_tf_instance({"os_type": "amazon-linux", "role": "victim"})["sftp_root_directory"]
            == ""
        )
