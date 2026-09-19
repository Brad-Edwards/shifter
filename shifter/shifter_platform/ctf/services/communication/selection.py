"""Apply complete-target visibility before counting or paging campaign results."""

from django.db.models import Exists, OuterRef

from ctf.enums import EventCapability
from ctf.models import CommunicationCampaign, CommunicationTargetEvent
from ctf.services.authorization import events_with_capability


def visible_campaigns(actor, *, workspace_id):
    """Return only campaigns whose entire persisted target set is accessible.

    The caller still rechecks live actor/token/workspace authority before emitting
    a page. A concurrent denial fails that request instead of exposing a hole.
    """
    events = events_with_capability(actor, capability=EventCapability.NOTIFICATIONS.value).filter(
        workspace_id=workspace_id
    )
    links = CommunicationTargetEvent.all_objects.filter(campaign_id=OuterRef("pk"))
    return CommunicationCampaign.objects.filter(workspace_id=workspace_id).filter(
        Exists(links), ~Exists(links.exclude(event_id__in=events.values("pk")))
    )
