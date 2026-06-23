"""Tests for Django settings module invariants."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.py"


def _load_settings_module(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, SETTINGS_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def test_production_settings_exempt_health_from_ssl_redirect(monkeypatch) -> None:
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "shifter-platform-tests-secret-key")
    monkeypatch.setenv("DJANGO_DEBUG", "false")

    settings_module = _load_settings_module("config._settings_production_redirect_test")

    assert settings_module.DEBUG is False
    assert settings_module.SECURE_SSL_REDIRECT is True
    assert settings_module.SECURE_REDIRECT_EXEMPT == [r"^health/?$"]


def test_portal_capacity_metrics_defaults_are_off(monkeypatch) -> None:
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "shifter-platform-tests-secret-key")
    for var in (
        "PORTAL_CAPACITY_METRICS_ENABLED",
        "PORTAL_CAPACITY_METRICS_INTERVAL_SECONDS",
        "PORTAL_WORKER_SOFT_CONCURRENCY",
        "PORTAL_CAPACITY_NAME_PREFIX",
    ):
        monkeypatch.delenv(var, raising=False)

    # The capacity settings live in a re-exported sub-module; evict it so the
    # fresh settings load re-reads the (cleared) environment instead of the cache.
    sys.modules.pop("config._capacity_settings", None)
    settings_module = _load_settings_module("config._settings_capacity_default_test")

    assert settings_module.PORTAL_CAPACITY_METRICS_ENABLED is False
    assert settings_module.PORTAL_CAPACITY_METRICS_INTERVAL_SECONDS == 60
    assert settings_module.PORTAL_WORKER_SOFT_CONCURRENCY == 6
    assert settings_module.PORTAL_CAPACITY_NAME_PREFIX == ""
    # The in-flight middleware must be wired into the request path.
    assert "config.middleware.RequestInFlightMiddleware" in settings_module.MIDDLEWARE


def test_portal_capacity_metrics_read_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "shifter-platform-tests-secret-key")
    monkeypatch.setenv("PORTAL_CAPACITY_METRICS_ENABLED", "true")
    monkeypatch.setenv("PORTAL_CAPACITY_METRICS_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("PORTAL_WORKER_SOFT_CONCURRENCY", "12")
    monkeypatch.setenv("PORTAL_CAPACITY_NAME_PREFIX", "prod-portal")

    # Re-read env in the extracted capacity-settings sub-module (avoid the cache).
    sys.modules.pop("config._capacity_settings", None)
    settings_module = _load_settings_module("config._settings_capacity_env_test")

    assert settings_module.PORTAL_CAPACITY_METRICS_ENABLED is True
    assert settings_module.PORTAL_CAPACITY_METRICS_INTERVAL_SECONDS == 30
    assert settings_module.PORTAL_WORKER_SOFT_CONCURRENCY == 12
    assert settings_module.PORTAL_CAPACITY_NAME_PREFIX == "prod-portal"


def test_api_token_policy_defaults(monkeypatch) -> None:
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "shifter-platform-tests-secret-key")
    for var in ("API_TOKEN_LAST_USED_COALESCE_SECONDS", "API_TOKEN_MAX_TTL_DAYS"):
        monkeypatch.delenv(var, raising=False)

    settings_module = _load_settings_module("config._settings_api_token_default_test")

    assert settings_module.API_TOKEN_LAST_USED_COALESCE_SECONDS == 300
    assert settings_module.API_TOKEN_MAX_TTL_DAYS == 365
    # The platform token authenticator is the first DRF default.
    assert (
        settings_module.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"][0]
        == "shared.api_tokens.authentication.ApiTokenAuthentication"
    )


def test_api_token_policy_read_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "shifter-platform-tests-secret-key")
    monkeypatch.setenv("API_TOKEN_LAST_USED_COALESCE_SECONDS", "60")
    monkeypatch.setenv("API_TOKEN_MAX_TTL_DAYS", "30")

    settings_module = _load_settings_module("config._settings_api_token_env_test")

    assert settings_module.API_TOKEN_LAST_USED_COALESCE_SECONDS == 60
    assert settings_module.API_TOKEN_MAX_TTL_DAYS == 30
