"""Tests for ``config.apps.PortalConfig.ready()`` startup bindings (#1374).

Verified through the real effect of Django having already called ``ready()``
at process startup (the established pattern in this repo — see
``tests/management/test_apps.py``) rather than by patching ``bind_audit_writer``
and asserting a call shape. ``ready()`` runs exactly once per test-worker
process, before any test executes, so these tests read the already-bound state.
"""

from __future__ import annotations

from config.cognito_groups import cognito_groups_provider
from shared.audit.groups_port import get_cognito_groups_provider
from shared.audit.port import get_audit_writer
from shared.audit_adapter import audit_log_writer


def test_ready_binds_the_shared_audit_adapter():
    """``PortalConfig.ready()`` bound ``shared.audit_adapter.audit_log_writer``.

    Proves the startup wiring moved with the adapter (#1374): the concrete
    writer resolved through the neutral port is the exact singleton now
    defined in ``shared.audit_adapter``, not a stale risk_register reference.
    """
    assert get_audit_writer() is audit_log_writer


def test_ready_binds_the_cognito_groups_provider():
    """``PortalConfig.ready()`` bound ``config.cognito_groups.cognito_groups_provider``.

    Proves the audit-read Cognito-group permission's session-predates-capture
    fallback resolves to the real ``management``-backed adapter (#1374
    fix-forward), not an unbound port.
    """
    assert get_cognito_groups_provider() is cognito_groups_provider
