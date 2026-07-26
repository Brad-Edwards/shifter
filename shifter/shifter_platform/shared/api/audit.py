"""Staff-session-only read API for durable audit events."""

from __future__ import annotations

import logging

from django.db.models import QuerySet
from rest_framework import permissions, serializers, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.request import Request
from rest_framework.views import APIView

from shared.api_tokens.models import ApiToken
from shared.audit import AuditAction, AuditEntityType, audit_log_from_request
from shared.models import AuditLog

logger = logging.getLogger(__name__)


class AuditLogSerializer(serializers.ModelSerializer):
    """Read-only audit-event representation."""

    # Historical rows can contain retired vocabulary, so these are strings
    # rather than a closed OpenAPI enum.
    entity_type = serializers.CharField(read_only=True)
    action = serializers.CharField(read_only=True)

    class Meta:
        """Bind the serializer to the immutable audit model."""

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
        read_only_fields = fields


class IsStaffAuditSession(permissions.BasePermission):
    """Admit staff browser sessions and audit every denial."""

    message = "This action requires a staff session."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = getattr(request, "user", None)
        allowed = bool(
            not isinstance(getattr(request, "auth", None), ApiToken)
            and user
            and user.is_authenticated
            and (user.is_staff or user.is_superuser)
        )
        if not allowed:
            try:
                audit_log_from_request(
                    request,
                    entity_type=AuditEntityType.CONFIG,
                    entity_id=0,
                    action=AuditAction.ACCESS_DENIED,
                    context=f"Permission denied: {type(view).__name__} - audit read requires staff session",
                )
            except Exception:
                logger.exception("Failed to record denied audit-read request")
        return allowed


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """List and retrieve audit events with the existing filter surface."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffAuditSession]
    serializer_class = AuditLogSerializer

    def get_queryset(self) -> QuerySet[AuditLog]:
        queryset = AuditLog.objects.all()
        filters = {
            "entity_type": "entity_type",
            "entity_id": "entity_id",
            "action": "action",
            "actor_type": "actor_type",
            "actor_id": "actor_id",
            "request_id": "request_id",
            "from_date": "timestamp__gte",
            "to_date": "timestamp__lte",
        }
        for parameter, field in filters.items():
            value = self.request.query_params.get(parameter)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset.order_by("-timestamp")
