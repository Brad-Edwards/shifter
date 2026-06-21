"""Startup posture logging for resolved Django settings (#948).

Follows the ``describe_*`` / ``log_*`` pattern established in
``config._channels`` for channel-layer observability (#849).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from shared.log_sanitize import safe_log_value

__all__ = [
    "describe_auth_posture",
    "describe_database_posture",
    "describe_deploy_posture",
    "describe_environment_posture",
    "log_settings_posture",
]

_logger = logging.getLogger(__name__)


def describe_environment_posture(env: Mapping[str, str]) -> dict[str, object]:
    return {
        "environment": env.get("ENVIRONMENT", "").strip() or None,
        "debug": env.get("DJANGO_DEBUG", "").strip().lower() == "true",
        "testing": env.get("TESTING") == "1",
    }


def describe_auth_posture(env: Mapping[str, str]) -> dict[str, object]:
    return {
        "auth_provider": env.get("AUTH_PROVIDER", "oidc").strip().lower() or "oidc",
    }


def describe_database_posture(env: Mapping[str, str]) -> dict[str, object]:
    if env.get("TESTING") == "1":
        return {"engine": "sqlite", "host": None, "port": None, "name": None}
    host = env.get("DB_HOST", "localhost").strip() or "localhost"
    return {
        "engine": "postgresql",
        "host": host,
        "port": env.get("DB_PORT", "5432").strip() or "5432",
        "name": env.get("DB_NAME", "shifter").strip() or "shifter",
    }


def describe_deploy_posture(env: Mapping[str, str]) -> dict[str, object]:
    local = env.get("LOCAL_PROVISIONER", "").strip()
    return {
        "cloud_provider": env.get("CLOUD_PROVIDER", "aws").strip().lower() or "aws",
        "local_provisioner": local or None,
        "deploy_mode": local if local else "remote",
    }


def log_settings_posture(env: Mapping[str, str], *, logger: logging.Logger | None = None) -> None:
    """Emit non-secret resolved posture for each subsystem at boot (#948 AC2)."""
    from config._channels import log_channel_layer_posture

    log = logger or _logger
    env_posture = describe_environment_posture(env)
    auth_posture = describe_auth_posture(env)
    db_posture = describe_database_posture(env)
    deploy_posture = describe_deploy_posture(env)

    log.info(
        "settings posture: environment=%s debug=%s testing=%s auth_provider=%s "
        "db_engine=%s db_host=%s db_port=%s db_name=%s cloud_provider=%s deploy_mode=%s local_provisioner=%s",
        safe_log_value(env_posture["environment"]),
        env_posture["debug"],
        env_posture["testing"],
        safe_log_value(auth_posture["auth_provider"]),
        safe_log_value(db_posture["engine"]),
        safe_log_value(db_posture["host"]),
        safe_log_value(db_posture["port"]),
        safe_log_value(db_posture["name"]),
        safe_log_value(deploy_posture["cloud_provider"]),
        safe_log_value(deploy_posture["deploy_mode"]),
        safe_log_value(deploy_posture["local_provisioner"]),
    )
    log_channel_layer_posture(env, logger=log)
