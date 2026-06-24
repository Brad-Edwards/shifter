"""Tests for DATABASES construction (issue #159).

Covers the SQLite test path and the deployed PostgreSQL path with and without
RDS IAM authentication.
"""

from __future__ import annotations

import pytest

from config._database_settings import _build_databases


def _clear_db_env(monkeypatch):
    for var in ("TESTING", "DB_IAM_AUTH", "DB_SSLMODE", "DB_SSL_ROOT_CERT"):
        monkeypatch.delenv(var, raising=False)


def test_testing_uses_sqlite(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("TESTING", "1")
    default = _build_databases()["default"]
    assert default["ENGINE"] == "django.db.backends.sqlite3"


def test_password_path_uses_stock_backend(monkeypatch):
    _clear_db_env(monkeypatch)
    default = _build_databases()["default"]
    assert default["ENGINE"] == "django.db.backends.postgresql"
    assert "sslmode" not in default["OPTIONS"]


def test_iam_path_uses_iam_backend_and_requires_ssl(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("DB_IAM_AUTH", "true")
    default = _build_databases()["default"]
    assert default["ENGINE"] == "config.db_backends.rds_iam"
    assert default["OPTIONS"]["sslmode"] == "require"
    assert "sslrootcert" not in default["OPTIONS"]


def test_iam_path_honours_sslmode_and_root_cert(monkeypatch):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("DB_IAM_AUTH", "true")
    monkeypatch.setenv("DB_SSLMODE", "verify-full")
    monkeypatch.setenv("DB_SSL_ROOT_CERT", "/etc/ssl/rds-ca.pem")
    options = _build_databases()["default"]["OPTIONS"]
    assert options["sslmode"] == "verify-full"
    assert options["sslrootcert"] == "/etc/ssl/rds-ca.pem"


@pytest.mark.parametrize("value", ["false", "0", "", "no"])
def test_iam_disabled_for_non_true_values(monkeypatch, value):
    _clear_db_env(monkeypatch)
    monkeypatch.setenv("DB_IAM_AUTH", value)
    assert _build_databases()["default"]["ENGINE"] == "django.db.backends.postgresql"
