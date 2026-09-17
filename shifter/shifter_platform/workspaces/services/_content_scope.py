"""Read-only organization scope for private content, separate from admin powers."""

from uuid import UUID

from workspaces.models import Organization, OrganizationMembership, WorkspaceMembership


def content_organization_uuids(actor) -> frozenset[UUID]:
    """Return current organization memberships, including active workspace seats.

    Staff status does not grant access. The explicit platform superuser override
    may inspect all organizations; inactive and anonymous callers see none.
    """
    if not getattr(actor, "is_authenticated", False) or not getattr(actor, "is_active", False):
        return frozenset()
    if actor.is_superuser:
        return frozenset(Organization.objects.values_list("uuid", flat=True))
    organizations = OrganizationMembership.objects.filter(user=actor).values_list("organization__uuid", flat=True)
    workspaces = WorkspaceMembership.objects.filter(user=actor, workspace__archived_at__isnull=True).values_list(
        "workspace__organization__uuid", flat=True
    )
    return frozenset((*organizations, *workspaces))
