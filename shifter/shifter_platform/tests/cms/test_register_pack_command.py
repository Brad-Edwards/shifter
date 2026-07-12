"""Operator ``register_pack`` management command (#1578, ADR-034).

The CLI is a thin operator entrypoint onto ``cms.services.register_pack``; the
service owns validation and authorization. These tests cover the wiring and the
error surface.
"""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from cms.models import AcesPackageSource

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_actor(django_user_model):
    return django_user_model.objects.create_user(
        username="cli-actor@example.com",
        email="cli-actor@example.com",
        is_staff=True,
    )


# A repo pack's catalog id is bound to its validated identity.
CLI_FIXTURE_NAME = "cli-fixture"


@pytest.fixture
def repo_pack(make_pack, tmp_path, monkeypatch):
    from django.conf import settings

    make_pack(tmp_path / "packs" / "fixture", name=CLI_FIXTURE_NAME)
    monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(tmp_path))
    return "packs/fixture"


def _args(package_ref: str, actor: str, **overrides) -> list[str]:
    values = {
        "--scenario-id": CLI_FIXTURE_NAME,
        "--source-kind": "repo",
        "--contract-profile": "shifter",
        "--package-ref": package_ref,
        "--package-version": "0.1.0",
        "--package-digest": "sha256:" + "a" * 64,
        "--actor": actor,
    }
    values.update(overrides)
    args: list[str] = []
    for flag, value in values.items():
        args.extend([flag, value])
    return args


def test_command_registers_pack(admin_actor, repo_pack):
    call_command("register_pack", *_args(repo_pack, admin_actor.username))
    assert AcesPackageSource.objects.filter(scenario_id=CLI_FIXTURE_NAME).exists()


def test_command_errors_on_unknown_actor(repo_pack):
    with pytest.raises(CommandError):
        call_command("register_pack", *_args(repo_pack, "nobody@example.com"))


def test_command_errors_on_domain_failure(admin_actor, repo_pack):
    # Shadowing a legacy scenario id is a fail-closed CMSError surfaced as a
    # CommandError, not a traceback. The message pins the shadow guard.
    with pytest.raises(CommandError, match="shadow"):
        call_command("register_pack", *_args(repo_pack, admin_actor.username, **{"--scenario-id": "basic"}))
