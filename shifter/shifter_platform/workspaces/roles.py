"""Closed workspace role and operation vocabularies (ADR-046-R2).

The role vocabulary is deliberately minimal here. #1325 establishes the tenancy
data model and its compatibility default; the wider role set and the
role-to-operation permission matrix belong to #1326 and are added by extending
these two enums plus :data:`ROLE_OPERATIONS`, not by introducing a second
membership store, a free-form role string, a Django ``auth.Group``, or an
API-token scope.
"""

from django.db import models


class WorkspaceRole(models.TextChoices):
    """Roles a user may hold in a workspace.

    ``OWNER`` is the only role #1325 needs: it is what every compatibility
    membership created for a personal workspace carries.
    """

    OWNER = "owner", "Owner"


class WorkspaceOperation(models.TextChoices):
    """Operations the workspace authorization seam can be asked about.

    Only operations exercised by shipped callers are listed. ``LAUNCH_RANGE``
    is checked by the CMS range-create facade and ``REASSIGN_RANGE`` by the CMS
    range-reassignment facade.
    """

    LAUNCH_RANGE = "launch_range", "Launch a range in the workspace"
    REASSIGN_RANGE = "reassign_range", "Reassign a range within the workspace"


#: Role-to-operation policy. #1326 extends this matrix; callers must not
#: re-derive permissions from a role code themselves.
ROLE_OPERATIONS: dict[str, frozenset[str]] = {
    WorkspaceRole.OWNER.value: frozenset(
        {
            WorkspaceOperation.LAUNCH_RANGE.value,
            WorkspaceOperation.REASSIGN_RANGE.value,
        }
    ),
}


def role_permits(role: str, operation: str) -> bool:
    """Return True when ``role`` is allowed to perform ``operation``.

    Unknown roles and unknown operations are denied: the policy is fail-closed,
    so a role code that predates a vocabulary change never silently gains
    authority.
    """
    return operation in ROLE_OPERATIONS.get(role, frozenset())
