"""Tests pinning the Risk Register feature removal (#1374 Part B).

The ``risk_register`` Django app, its models, views, API, and templates are
deleted outright (the audit subsystem and the archival ``APIKey`` model were
already rehomed to ``shared`` before this app was removed). These tests prove
the removal is real and structural, not merely "unused": the package cannot be
imported, it is absent from ``INSTALLED_APPS``, and the routes it used to
serve resolve to a plain 404 rather than an access-denied page implying a
hidden product still exists.
"""

from __future__ import annotations

import importlib

import pytest
from django.conf import settings
from django.test import Client
from django.urls import Resolver404, resolve


def test_risk_register_is_not_importable() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("risk_register")


def test_risk_register_absent_from_installed_apps() -> None:
    assert "risk_register" not in " ".join(settings.INSTALLED_APPS)
    assert not any(app.startswith("risk_register.") for app in settings.INSTALLED_APPS)


@pytest.mark.django_db
def test_risks_api_returns_404() -> None:
    assert Client().get("/api/v1/risks/").status_code == 404


def test_risks_api_is_unroutable() -> None:
    with pytest.raises(Resolver404):
        resolve("/api/v1/risks/")


@pytest.mark.django_db
def test_legacy_risk_register_page_returns_404() -> None:
    assert Client().get("/risk-register/").status_code == 404


def test_legacy_risk_register_page_is_unroutable() -> None:
    with pytest.raises(Resolver404):
        resolve("/risk-register/")


def test_risk_register_cognito_group_setting_removed() -> None:
    # Part C: deleted outright rather than renamed into a disguised audit
    # knob (preflight decision #1).
    assert not hasattr(settings, "RISK_REGISTER_ALLOWED_COGNITO_GROUPS")


def test_risk_register_spa_flag_setting_removed() -> None:
    assert not hasattr(settings, "RISK_REGISTER_SPA_ENABLED")
