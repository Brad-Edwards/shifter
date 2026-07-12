"""In-box catalog bootstrap through the uniform ingestion path (#1578, ADR-034).

The in-box catalog is loaded through the SAME ``register_pack`` service an
operator uses — there is no privileged code path. There are no conformant default
packs yet, so the shipped manifest is empty; these tests prove the mechanism
end-to-end with a temporary manifest and confirm the shipped manifest stays a
valid, empty declaration.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from django.contrib.auth import get_user_model

from cms.models import AcesPackageSource
from cms.scenarios.inbox import SHIPPED_INBOX_MANIFEST, load_inbox_manifest, register_inbox_packs
from cms.scenarios.registry import get_catalog_entry

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_actor(db):
    return User.objects.create_user(
        username="inbox-bootstrap@example.com",
        email="inbox-bootstrap@example.com",
        is_staff=True,
    )


def _write_manifest(path: Path, entries: list[dict]) -> Path:
    path.write_text(yaml.safe_dump({"packs": entries}), encoding="utf-8")
    return path


def _inbox_entry(package_ref: str, **overrides) -> dict:
    entry = {
        "scenario_id": "inbox-fixture",
        "source_kind": "repo",
        "contract_kind": "aces",
        "contract_profile": "shifter",
        "package_ref": package_ref,
        "package_version": "0.1.0",
        "package_digest": "sha256:" + "a" * 64,
        "provenance": {"repo": "Brad-Edwards/shifter"},
    }
    entry.update(overrides)
    return entry


class TestShippedManifest:
    def test_shipped_manifest_exists_and_parses_to_a_list(self):
        packs = load_inbox_manifest(SHIPPED_INBOX_MANIFEST)
        assert isinstance(packs, list)

    def test_shipped_manifest_is_empty_no_default_packs_yet(self):
        # No conformant default scenario packs ship yet (program #1584).
        assert load_inbox_manifest(SHIPPED_INBOX_MANIFEST) == []


class TestRegisterInboxPacks:
    def test_registers_each_entry_through_the_service(self, admin_actor, make_pack, tmp_path, monkeypatch):
        from django.conf import settings

        make_pack(tmp_path / "packs" / "fixture", name="inbox-fixture")
        monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(tmp_path))
        manifest = _write_manifest(tmp_path / "manifest.yaml", [_inbox_entry("packs/fixture")])

        registered = register_inbox_packs(actor=admin_actor, manifest_path=manifest)

        assert [r.scenario_id for r in registered] == ["inbox-fixture"]
        assert AcesPackageSource.objects.filter(scenario_id="inbox-fixture").exists()
        assert get_catalog_entry("inbox-fixture") is not None

    def test_is_idempotent(self, admin_actor, make_pack, tmp_path, monkeypatch):
        from django.conf import settings

        make_pack(tmp_path / "packs" / "fixture", name="inbox-fixture")
        monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(tmp_path))
        manifest = _write_manifest(tmp_path / "manifest.yaml", [_inbox_entry("packs/fixture")])

        first = register_inbox_packs(actor=admin_actor, manifest_path=manifest)
        second = register_inbox_packs(actor=admin_actor, manifest_path=manifest)

        assert len(first) == 1
        assert second == []  # already-registered entries are skipped
        assert AcesPackageSource.objects.filter(scenario_id="inbox-fixture").count() == 1

    def test_empty_manifest_is_a_noop(self, admin_actor, tmp_path):
        manifest = _write_manifest(tmp_path / "manifest.yaml", [])
        assert register_inbox_packs(actor=admin_actor, manifest_path=manifest) == []

    def test_absent_manifest_is_a_noop(self, admin_actor, tmp_path):
        assert register_inbox_packs(actor=admin_actor, manifest_path=tmp_path / "nope.yaml") == []


class TestBootstrapCommand:
    def test_command_runs_against_shipped_empty_manifest(self, admin_actor):
        from django.core.management import call_command

        # The shipped manifest is empty today: the command is a clean no-op.
        call_command("bootstrap_inbox_catalog", "--actor", admin_actor.username)
        assert AcesPackageSource.objects.count() == 0

    def test_command_errors_on_unknown_actor(self, db):
        from django.core.management import CommandError, call_command

        with pytest.raises(CommandError):
            call_command("bootstrap_inbox_catalog", "--actor", "nobody@example.com")
