"""Workspace model - the tenancy scope ranges are bound to (ADR-046)."""

import uuid

from django.conf import settings
from django.db import models


class Workspace(models.Model):
    """A scope inside an organization that holds members and owns range scope.

    ``organization`` is an intra-domain ForeignKey and is required: there is no
    such thing as a workspace outside an organization. Cross-layer references
    to a workspace are the *scalar* ``workspace_id`` columns on
    ``cms.Request``, ``cms.RangeInstance``, and ``engine.Range``; those layers
    must not gain a ForeignKey here (ADR-001-R2, ADR-046-R1).

    ``personal_for_user`` marks the compatibility workspace that #1325 creates
    for each existing and new user. It is nullable and unique, so a user has at
    most one personal workspace while ordinary shared workspaces are unlimited.
    There is deliberately no shared deployment-global "Default" workspace: the
    compatibility default is per user (ADR-046-R4).

    A workspace has no ``owner_user`` field. Ownership is a membership row, so
    there is exactly one place authority is recorded; the last-owner invariant
    is enforced transactionally by ``workspaces.services``.
    """

    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Immutable public identifier; the only workspace ID public surfaces accept.",
    )
    organization = models.ForeignKey(
        "workspaces.Organization",
        on_delete=models.CASCADE,
        related_name="workspaces",
        help_text="Owning organization (intra-domain FK; always set).",
    )
    name = models.CharField(max_length=200, help_text="Display name, unique within the organization.")
    personal_for_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="personal_workspace",
        help_text=(
            "Set when this is the user's personal compatibility workspace (#1325). "
            "NULL for ordinary shared workspaces; unique so a user has at most one."
        ),
    )
    archived_at = models.DateTimeField(
        null=True,
        blank=True,
        default=None,
        db_index=True,
        help_text=(
            "Set when the workspace is archived; NULL for active workspaces. "
            "A reversible lifecycle marker only -- archival never deletes or "
            "rehomes ranges bound to the workspace (#1940, PLAT-233)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        db_table = "workspaces_workspace"
        ordering = ["organization_id", "name", "id"]
        verbose_name = "Workspace"
        verbose_name_plural = "Workspaces"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="uniq_workspace_name_per_organization",
            ),
        ]

    def __str__(self) -> str:
        """Return a compact diagnostic representation."""
        return self.name
