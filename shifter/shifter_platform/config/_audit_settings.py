"""Audit-log read-authorization settings (#1374 fix-forward).

Extracted from ``config/settings.py`` to keep that module under the 500-line
cap (Sonar S104), following the established ``_oidc_settings.py`` /
``_rate_limit_settings.py`` split-settings pattern. Reads the same shape of
environment variable the retired ``RISK_REGISTER_ALLOWED_COGNITO_GROUPS``
knob used, under an audit-owned name: see
``shared.audit.access.allowed_audit_log_cognito_groups`` and
``shared.api.permissions.HasAuditLogCognitoGroup`` for where this is
consumed and enforced.
"""

from __future__ import annotations

import os
import warnings

from config._runtime_env import IS_TEST_RUN

__all__ = ["AUDIT_LOG_ALLOWED_COGNITO_GROUPS"]


def _env_list(name: str) -> list[str]:
    """Parse comma-separated environment variables into stripped string lists."""
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


# Cognito group gate restoring the pre-#1374 risk-register-owned compound
# authorization for the platform audit-read API (``/api/v1/audit/``) under an
# audit-owned name. Fail closed when unset: an unconfigured deployment grants
# no audit-read access to anyone, staff/superuser included (see
# ``shared.api.permissions.HasAuditLogCognitoGroup``).
AUDIT_LOG_ALLOWED_COGNITO_GROUPS = _env_list("AUDIT_LOG_ALLOWED_COGNITO_GROUPS")
if not AUDIT_LOG_ALLOWED_COGNITO_GROUPS and not IS_TEST_RUN:
    warnings.warn(
        "AUDIT_LOG_ALLOWED_COGNITO_GROUPS is unset; audit log read access is denied for all "
        "principals, staff and superusers included. This variable replaces the retired "
        "RISK_REGISTER_ALLOWED_COGNITO_GROUPS (#1374): a deployment still supplying only the old "
        "name starts cleanly but returns 403 for every /api/v1/audit/ read. Set the new name to "
        "restore audit-read access.",
        RuntimeWarning,
        stacklevel=2,
    )
