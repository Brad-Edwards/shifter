"""Personal-workspace provisioning: the #1325 compatibility default (ADR-046-R4).

Every user owns exactly one personal workspace inside its own personal
organization. That is what keeps a single-user install behaving identically
after the tenancy layer lands, without baking a deployment-global "Default"
organization into the schema -- a shared default would make every install
single-tenant by construction and would have to be unpicked before a university
or hosting operator could run more than one tenant.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction

from workspaces.models import Organization, Workspace, WorkspaceMembership
from workspaces.roles import WorkspaceRole

from ._authorization import WorkspaceAuthorization

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

#: Personal organizations and workspaces are identified by their
#: ``personal_for_user`` link, never by a parsed display name, so the label
#: carries no username or email.
PERSONAL_NAME = "Personal"


def _authorization_for(workspace: Workspace, role: str) -> WorkspaceAuthorization:
    """Build the immutable result for an owned personal workspace."""
    return WorkspaceAuthorization(
        workspace_id=workspace.pk,
        workspace_uuid=workspace.uuid,
        organization_id=workspace.organization_id,
        role=role,
    )


def resolve_personal_workspace(user: User) -> WorkspaceAuthorization:
    """Return ``user``'s personal workspace, creating it on first use.

    Idempotent and atomic: the organization, the workspace, and the owner
    membership are created together or not at all, so a failure part-way
    through never leaves a workspace without an owner. Concurrent first calls
    for the same user race on the unique ``personal_for_user`` column; the
    loser re-reads the winner's row.

    Returns:
        WorkspaceAuthorization: the owner-role authorization for the workspace.
    """
    existing = Workspace.objects.filter(personal_for_user=user).first()
    if existing is not None:
        return _authorization_for(existing, WorkspaceRole.OWNER.value)

    try:
        with transaction.atomic():
            organization = Organization.objects.create(name=PERSONAL_NAME)
            workspace = Workspace.objects.create(
                organization=organization,
                name=PERSONAL_NAME,
                personal_for_user=user,
            )
            WorkspaceMembership.objects.create(
                workspace=workspace,
                user=user,
                role=WorkspaceRole.OWNER.value,
            )
    except Exception:
        # A concurrent caller may have won the unique personal_for_user race.
        concurrent = Workspace.objects.filter(personal_for_user=user).first()
        if concurrent is None:
            raise
        logger.debug("resolve_personal_workspace: reusing concurrently created workspace user_id=%s", user.pk)
        return _authorization_for(concurrent, WorkspaceRole.OWNER.value)

    logger.info("resolve_personal_workspace: created personal workspace user_id=%s", user.pk)
    return _authorization_for(workspace, WorkspaceRole.OWNER.value)
