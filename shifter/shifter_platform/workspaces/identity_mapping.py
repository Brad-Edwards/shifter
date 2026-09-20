"""Read-only, fail-closed classification of legacy customer structures.

The S8 migrator supplies complete owner evidence from owning domains. This
module never edits, deletes, or rehomes a legacy organization or workspace.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from workspaces.models import Organization, OrganizationMembership, Workspace, WorkspaceMembership


@dataclass(frozen=True, slots=True)
class MappingBlocker:
    reason: str
    entity_id: int


@dataclass(frozen=True, slots=True)
class IndividualMapping:
    principal_uuid: UUID
    user_id: int
    legacy_organization_id: int
    legacy_workspace_id: int


@dataclass(frozen=True, slots=True)
class OrganizationMapping:
    organization_id: int
    account_kind: str
    default_workspace_id: int | None


@dataclass(frozen=True, slots=True)
class MembershipMapping:
    scope_kind: str
    scope_id: int
    principal_uuid: UUID
    role: str


@dataclass(frozen=True, slots=True)
class LegacyAccountMappingPlan:
    individuals: tuple[IndividualMapping, ...]
    organizations: tuple[OrganizationMapping, ...]
    memberships: tuple[MembershipMapping, ...]
    blockers: tuple[MappingBlocker, ...]


def _classify_personal(
    organization: Organization,
    workspace: Workspace,
    principal_by_user: Mapping[int, UUID],
    owner_ids_by_workspace: Mapping[int, set[int]],
) -> tuple[IndividualMapping | None, list[MappingBlocker]]:
    user_id = workspace.personal_for_user_id
    if user_id is None:
        return None, [MappingBlocker("personal_structure_ambiguous", workspace.pk)]
    blockers: list[MappingBlocker] = []
    members = list(WorkspaceMembership.objects.filter(workspace=workspace).values_list("user_id", "role"))
    admins = list(OrganizationMembership.objects.filter(organization=organization).values_list("user_id", "role"))
    if members != [(user_id, "owner")] or admins != [(user_id, "admin")]:
        blockers.append(MappingBlocker("personal_membership_ambiguous", workspace.pk))
    if workspace.pk not in owner_ids_by_workspace:
        blockers.append(MappingBlocker("resource_owner_evidence_missing", workspace.pk))
    elif set(owner_ids_by_workspace[workspace.pk]) - {user_id}:
        blockers.append(MappingBlocker("resource_owner_mismatch", workspace.pk))
    principal_uuid = principal_by_user.get(user_id)
    if not isinstance(principal_uuid, UUID) or principal_uuid.int == 0:
        blockers.append(MappingBlocker("principal_missing", user_id))
    if blockers or not isinstance(principal_uuid, UUID):
        return None, blockers
    return IndividualMapping(principal_uuid, user_id, organization.pk, workspace.pk), []


def _classify_shared_memberships(
    organization: Organization,
    workspaces: list[Workspace],
    principal_by_user: Mapping[int, UUID],
) -> tuple[list[MembershipMapping], list[MappingBlocker]]:
    mappings: list[MembershipMapping] = []
    blockers: list[MappingBlocker] = []
    rows = [
        ("organization", organization.pk, member.user_id, member.role)
        for member in OrganizationMembership.objects.filter(organization=organization).order_by("pk")
    ]
    for workspace in workspaces:
        rows.extend(
            ("workspace", workspace.pk, member.user_id, member.role)
            for member in WorkspaceMembership.objects.filter(workspace=workspace).order_by("pk")
        )
    for scope_kind, scope_id, user_id, role in rows:
        principal_uuid = principal_by_user.get(user_id)
        if not isinstance(principal_uuid, UUID) or principal_uuid.int == 0:
            blockers.append(MappingBlocker("principal_missing", user_id))
        else:
            mappings.append(MembershipMapping(scope_kind, scope_id, principal_uuid, role))
    return mappings, blockers


def plan_legacy_account_mapping(
    *,
    principal_by_user: Mapping[int, UUID],
    owner_ids_by_workspace: Mapping[int, set[int]],
    account_types_by_organization: Mapping[UUID, str],
) -> LegacyAccountMappingPlan:
    """Classify unbound rows using exact user, UUID, membership, and owner facts.

    The caller must supply complete CMS/Engine/CTF owner evidence keyed by the
    existing workspace primary key. An empty owner set means no scoped owner
    rows; it is never a permission grant or installation scope.
    """
    individuals: list[IndividualMapping] = []
    organizations: list[OrganizationMapping] = []
    memberships: list[MembershipMapping] = []
    blockers: list[MappingBlocker] = []

    seen_principals: set[UUID] = set()
    for user_id, principal_uuid in principal_by_user.items():
        if not isinstance(principal_uuid, UUID) or principal_uuid.int == 0:
            blockers.append(MappingBlocker("principal_invalid", user_id))
        elif principal_uuid in seen_principals:
            blockers.append(MappingBlocker("duplicate_principal_mapping", user_id))
        else:
            seen_principals.add(principal_uuid)
    if blockers:
        return LegacyAccountMappingPlan((), (), (), tuple(blockers))

    for organization in Organization.objects.filter(account__isnull=True).order_by("pk"):
        workspaces = list(Workspace.objects.filter(organization=organization).order_by("pk"))
        personal = [workspace for workspace in workspaces if workspace.personal_for_user_id is not None]
        if personal:
            if len(workspaces) != 1 or len(personal) != 1:
                blockers.append(MappingBlocker("personal_structure_ambiguous", organization.pk))
                continue
            mapped, personal_blockers = _classify_personal(
                organization, personal[0], principal_by_user, owner_ids_by_workspace
            )
            blockers.extend(personal_blockers)
            if mapped is not None:
                individuals.append(mapped)
            continue

        account_kind = account_types_by_organization.get(organization.uuid)
        if account_kind not in ("team", "enterprise"):
            blockers.append(MappingBlocker("account_type_missing", organization.pk))
            continue
        if not workspaces:
            blockers.append(MappingBlocker("default_workspace_missing", organization.pk))
            continue
        if len(workspaces) > 1:
            blockers.append(MappingBlocker("default_workspace_ambiguous", organization.pk))
            continue
        if any(workspace.pk not in owner_ids_by_workspace for workspace in workspaces):
            blockers.append(MappingBlocker("resource_owner_evidence_missing", organization.pk))
            continue
        if any(
            user_id not in principal_by_user
            for workspace in workspaces
            for user_id in owner_ids_by_workspace[workspace.pk]
        ):
            blockers.append(MappingBlocker("principal_missing_for_resource_owner", organization.pk))
            continue
        mapped_members, member_blockers = _classify_shared_memberships(organization, workspaces, principal_by_user)
        blockers.extend(member_blockers)
        if member_blockers:
            continue
        organizations.append(OrganizationMapping(organization.pk, account_kind, workspaces[0].pk))
        memberships.extend(mapped_members)

    return LegacyAccountMappingPlan(tuple(individuals), tuple(organizations), tuple(memberships), tuple(blockers))
