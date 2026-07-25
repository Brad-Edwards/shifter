"""Neutral audit boundary for the whole platform (#1523, ADR-001).

``shared.audit`` is the single audit contract every layer emits through. It owns
the canonical vocabulary, the event/context shapes, the writer port + startup
binding, the strict/best-effort emission policy, trusted request attribution,
and process-local health. The concrete persistence adapter lives in
``shared.audit_adapter`` (rehomed from ``risk_register`` in #1374) and is bound
to the port at startup; emitters never import a feature domain to record audit
events.
"""

from __future__ import annotations

from shared.audit.access import (
    allowed_audit_log_cognito_groups,
    cognito_groups_for_request,
    log_audit_log_groups_unconfigured,
)
from shared.audit.attribution import (
    get_actor_from_request,
    get_client_ip,
    get_request_id,
    select_trusted_client_ip,
)
from shared.audit.events import (
    AuditEvent,
    AuthPrincipal,
    RequestAudit,
    SessionInfo,
    StateChange,
)
from shared.audit.groups_port import (
    CognitoGroupsProvider,
    CognitoGroupsProviderBindingError,
    bind_cognito_groups_provider,
    get_cognito_groups_provider,
    reset_cognito_groups_provider,
)
from shared.audit.health import (
    AuditHealthSnapshot,
    get_audit_health_snapshot,
    mark_audit_degraded,
    reset_audit_health,
)
from shared.audit.policy import (
    audit_auth_event,
    audit_log,
    audit_log_from_request,
    audit_log_system_event,
    audit_role_sync,
    audit_session_event,
)
from shared.audit.port import (
    AuditWriter,
    AuditWriterBindingError,
    bind_audit_writer,
    get_audit_writer,
    reset_audit_writer,
)
from shared.audit.vocabulary import (
    API_KEY_LABEL,
    AuditAction,
    AuditActorType,
    AuditEntityType,
)

__all__ = [
    "API_KEY_LABEL",
    "AuditAction",
    "AuditActorType",
    "AuditEntityType",
    "AuditEvent",
    "AuditHealthSnapshot",
    "AuditWriter",
    "AuditWriterBindingError",
    "AuthPrincipal",
    "CognitoGroupsProvider",
    "CognitoGroupsProviderBindingError",
    "RequestAudit",
    "SessionInfo",
    "StateChange",
    "allowed_audit_log_cognito_groups",
    "audit_auth_event",
    "audit_log",
    "audit_log_from_request",
    "audit_log_system_event",
    "audit_role_sync",
    "audit_session_event",
    "bind_audit_writer",
    "bind_cognito_groups_provider",
    "cognito_groups_for_request",
    "get_actor_from_request",
    "get_audit_health_snapshot",
    "get_audit_writer",
    "get_client_ip",
    "get_cognito_groups_provider",
    "get_request_id",
    "log_audit_log_groups_unconfigured",
    "mark_audit_degraded",
    "reset_audit_health",
    "reset_audit_writer",
    "reset_cognito_groups_provider",
    "select_trusted_client_ip",
]
