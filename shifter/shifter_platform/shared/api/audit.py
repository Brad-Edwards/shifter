"""Read-only platform audit log API (#1374 rehome from risk_register, #1523).

Mounted at ``/api/v1/audit/`` from ``config/api_urls.py`` via ``shared.api.urls``.
Session only (ADR-029): no platform API token scope is accepted for audit
reads. Authorization is a compound gate restoring the pre-#1374 risk-register
semantics under an audit-owned name -- the session principal must be BOTH
staff/superuser AND a member of a configured Cognito group
(``AUDIT_LOG_ALLOWED_COGNITO_GROUPS``); see
``shared.api.permissions.HasAuditLogCognitoGroup``.
"""

from __future__ import annotations

from rest_framework import serializers, viewsets

from shared.api.permissions import HasAuditLogCognitoGroup, IsStaffSessionAudited
from shared.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    """Serializer for AuditLog model (read-only).

    ``entity_type`` and ``action`` are declared as plain, un-enumerated
    strings rather than inheriting the model's ``choices``: historical rows
    carry retired values (``"risk"``, ``"comment"``) that are no longer part
    of the active vocabulary, and the published OpenAPI contract must not
    advertise a closed enum that existing data violates (#1374).
    """

    entity_type = serializers.CharField(read_only=True)
    action = serializers.CharField(read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "entity_type",
            "entity_id",
            "action",
            "actor_type",
            "actor_id",
            "timestamp",
            "previous_state",
            "new_state",
            "context",
            "source_ip",
            "user_agent",
            "request_id",
        ]
        read_only_fields = [
            "id",
            "entity_id",
            "actor_type",
            "actor_id",
            "timestamp",
            "previous_state",
            "new_state",
            "context",
            "source_ip",
            "user_agent",
            "request_id",
        ]


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only audit log queries for administrators.

    Provides list and detail views over the platform audit trail, filterable by
    entity_type, entity_id, action, actor_type, actor_id, and date range.
    Requires a staff or superuser session that is also a member of an allowed
    audit Cognito group; API tokens are not accepted.
    """

    # This docstring is the published OpenAPI description for /api/v1/audit/
    # (drf-spectacular derives it), so it states the authorization requirement in
    # caller-facing terms and leaves the mechanism here: DRF requires every class
    # in `permission_classes` to pass, so the two gates below are ANDed, and
    # `HasAuditLogCognitoGroup` fails closed when the allow-list is unconfigured.
    serializer_class = AuditLogSerializer
    permission_classes = [HasAuditLogCognitoGroup, IsStaffSessionAudited]

    def get_queryset(self):
        """Return audit logs with optional filtering."""
        queryset = AuditLog.objects.all()

        # Filter by entity_type
        entity_type = self.request.query_params.get("entity_type")
        if entity_type:
            queryset = queryset.filter(entity_type=entity_type)

        # Filter by entity_id
        entity_id = self.request.query_params.get("entity_id")
        if entity_id:
            queryset = queryset.filter(entity_id=entity_id)

        # Filter by action
        action = self.request.query_params.get("action")
        if action:
            queryset = queryset.filter(action=action)

        # Filter by actor_type
        actor_type = self.request.query_params.get("actor_type")
        if actor_type:
            queryset = queryset.filter(actor_type=actor_type)

        # Filter by actor_id
        actor_id = self.request.query_params.get("actor_id")
        if actor_id:
            queryset = queryset.filter(actor_id=actor_id)

        # Filter by request_id for trace correlation
        request_id = self.request.query_params.get("request_id")
        if request_id:
            queryset = queryset.filter(request_id=request_id)

        # Filter by date range
        from_date = self.request.query_params.get("from_date")
        if from_date:
            queryset = queryset.filter(timestamp__gte=from_date)

        to_date = self.request.query_params.get("to_date")
        if to_date:
            queryset = queryset.filter(timestamp__lte=to_date)

        return queryset.order_by("-timestamp")
