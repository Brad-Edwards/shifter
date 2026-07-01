"""Regression checks for the removed legacy experiments feature."""

from django.conf import settings
from django.urls import Resolver404, resolve

from shared.api_tokens import scopes


def test_legacy_experiments_app_is_not_installed():
    assert "cms.experiments.apps.ExperimentsConfig" not in settings.INSTALLED_APPS


def test_legacy_experiments_runtime_contract_is_removed():
    assert not hasattr(settings, "EXPERIMENTS_ENABLED")
    assert "experiments" not in settings.QUEUE_CONFIG


def test_legacy_script_surfaces_are_not_routed():
    for path in (
        "/mission-control/files/",
        "/mission-control/api/scripts/",
        "/api/v1/mission-control/scripts/",
        "/api/v1/mission-control/scripts/upload/",
    ):
        try:
            resolve(path)
        except Resolver404:
            continue
        raise AssertionError(f"legacy experiment script path still resolves: {path}")


def test_legacy_script_token_scopes_are_removed():
    assert "mission_control:script:read" not in scopes.KNOWN_SCOPES
    assert "mission_control:script:write" not in scopes.KNOWN_SCOPES
