"""Server-derived event access-role/capability projection mixin (#1922).

Shared by the organizer event summary and detail serializers so both expose the
same ``access_role`` / ``access_capabilities`` hints without duplicating the
projection. Presentation only, never authorization: a UI hides or disables
controls the requesting actor cannot use, but every mutation is still checked
server-side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

if TYPE_CHECKING:
    from ctf.models import CTFEvent


class EventAccessProjectionMixin(serializers.Serializer):
    """Add server-derived ``access_role`` + ``access_capabilities`` fields to an event serializer."""

    access_role = serializers.SerializerMethodField()
    access_capabilities = serializers.SerializerMethodField()

    def _projection(self, event: CTFEvent) -> tuple[str | None, list[str]]:
        """Resolve the requesting actor's access role and advisory capabilities."""
        from ctf.services.event import event_access_projection

        request = self.context.get("request")
        actor_id = getattr(getattr(request, "user", None), "pk", None)
        return event_access_projection(actor_id, event)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_access_role(self, event: CTFEvent) -> str | None:
        """Return the requesting actor's access role on this event."""
        return self._projection(event)[0]

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_access_capabilities(self, event: CTFEvent) -> list[str]:
        """Return the requesting actor's advisory capabilities on this event."""
        return self._projection(event)[1]
