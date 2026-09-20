"""Account hierarchy and explicit scope resolution (ADR-066)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.db import transaction

from shared.audit import AuditAction, AuditEntityType, AuditEvent, audit_log
from shared.identity_scope import PrincipalRef, ResourceScope
from workspaces.models import Account, AccountMembership, Organization, Workspace

_DENIED = "Scope unavailable"
_DEFAULT_NAME = "Default"


def _audit_create(entity_type: str, entity_id: int, state: dict[str, object]) -> None:
    """Keep structural mutation evidence bounded and in the caller transaction."""
    audit_log(
        AuditEvent(
            entity_type=entity_type,
            entity_id=entity_id,
            action=AuditAction.CREATE,
            new_state=state,
            context="account_hierarchy",
        ),
        strict=True,
    )


class AccountScopeError(ValueError):
    """An account scope is absent, malformed, or has invalid ancestry."""


@dataclass(frozen=True, slots=True)
class AccountView:
    id: int
    uuid: UUID
    kind: str
    individual_principal_uuid: UUID | None


@dataclass(frozen=True, slots=True)
class OrganizationView:
    id: int
    uuid: UUID
    account_id: int
    is_default: bool


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    id: int
    uuid: UUID
    organization_id: int
    is_default: bool


@dataclass(frozen=True, slots=True)
class ResolvedResourceScope:
    kind: str
    account_id: int | None
    organization_id: int | None
    workspace_id: int | None


def _account_view(account: Account) -> AccountView:
    return AccountView(account.id, account.uuid, account.kind, account.individual_principal_uuid)


def _organization_view(organization: Organization) -> OrganizationView:
    return OrganizationView(organization.id, organization.uuid, organization.account_id, organization.is_default)


def _workspace_view(workspace: Workspace) -> WorkspaceView:
    return WorkspaceView(workspace.id, workspace.uuid, workspace.organization_id, workspace.is_default)


def _locked_account(account_uuid: UUID) -> Account:
    account = Account.objects.select_for_update().filter(uuid=account_uuid).first()
    if account is None:
        raise AccountScopeError(_DENIED)
    return account


def _ensure_defaults_locked(account: Account) -> None:
    if account.kind == Account.Kind.INDIVIDUAL:
        if Organization.objects.filter(account=account).exists():
            raise AccountScopeError(_DENIED)
        return
    if account.kind not in (Account.Kind.TEAM, Account.Kind.ENTERPRISE):
        raise AccountScopeError(_DENIED)
    organization = Organization.objects.filter(account=account, is_default=True).first()
    if organization is None:
        organization = Organization.objects.create(account=account, name=_DEFAULT_NAME, is_default=True)
        _audit_create(AuditEntityType.ORGANIZATION, organization.pk, {"account_id": account.pk, "default": True})
    if not Workspace.objects.filter(organization=organization, is_default=True).exists():
        workspace = Workspace.objects.create(organization=organization, name=_DEFAULT_NAME, is_default=True)
        _audit_create(AuditEntityType.WORKSPACE, workspace.pk, {"organization_id": organization.pk, "default": True})


def create_account(*, kind: str, name: str, owner: PrincipalRef | None = None) -> AccountView:
    """Create a typed account and its structural defaults without any grant."""
    if kind not in Account.Kind.values or not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise AccountScopeError(_DENIED)
    if (kind == Account.Kind.INDIVIDUAL and (owner is None or owner.kind != "human")) or (
        kind != Account.Kind.INDIVIDUAL and owner is not None
    ):
        raise AccountScopeError(_DENIED)
    with transaction.atomic():
        account = Account.objects.create(
            kind=kind,
            name=name.strip(),
            individual_principal_uuid=owner.uuid if owner is not None else None,
        )
        _audit_create(AuditEntityType.ACCOUNT, account.pk, {"account_id": account.pk, "kind": account.kind})
        _ensure_defaults_locked(account)
    return _account_view(account)


def ensure_default_hierarchy(account_uuid: UUID) -> AccountView:
    """Idempotently ensure the type-required structure under the account lock."""
    with transaction.atomic():
        account = _locked_account(account_uuid)
        _ensure_defaults_locked(account)
        return _account_view(account)


def create_account_organization(account_uuid: UUID, name: str) -> OrganizationView:
    """Create one additional organization and its default workspace, no grant."""
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise AccountScopeError(_DENIED)
    with transaction.atomic():
        account = _locked_account(account_uuid)
        if account.kind == Account.Kind.INDIVIDUAL:
            raise AccountScopeError(_DENIED)
        organization = Organization.objects.create(account=account, name=name.strip())
        _audit_create(AuditEntityType.ORGANIZATION, organization.pk, {"account_id": account.pk, "default": False})
        workspace = Workspace.objects.create(organization=organization, name=_DEFAULT_NAME, is_default=True)
        _audit_create(AuditEntityType.WORKSPACE, workspace.pk, {"organization_id": organization.pk, "default": True})
        return _organization_view(organization)


def create_account_workspace(account_uuid: UUID, organization_uuid: UUID, name: str) -> WorkspaceView:
    """Create an additional workspace after proving its account ancestry."""
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise AccountScopeError(_DENIED)
    with transaction.atomic():
        account = _locked_account(account_uuid)
        organization = Organization.objects.select_for_update().filter(uuid=organization_uuid, account=account).first()
        if organization is None or account.kind == Account.Kind.INDIVIDUAL:
            raise AccountScopeError(_DENIED)
        workspace = Workspace.objects.create(organization=organization, name=name.strip())
        _audit_create(AuditEntityType.WORKSPACE, workspace.pk, {"organization_id": organization.pk, "default": False})
        return _workspace_view(workspace)


def add_account_member(account_uuid: UUID, principal: PrincipalRef) -> None:
    """Record explicit membership without assigning a role or policy."""
    if not isinstance(principal, PrincipalRef):
        raise AccountScopeError(_DENIED)
    with transaction.atomic():
        account = _locked_account(account_uuid)
        membership, created = AccountMembership.objects.get_or_create(account=account, principal_uuid=principal.uuid)
        if created:
            _audit_create(AuditEntityType.ACCOUNT_MEMBERSHIP, membership.pk, {"account_id": account.pk})


def resolve_resource_scope(scope: ResourceScope) -> ResolvedResourceScope:
    """Validate account type and exact ancestry; never infer installation scope."""
    if not isinstance(scope, ResourceScope):
        raise AccountScopeError(_DENIED)
    if scope.kind == "installation":
        return ResolvedResourceScope("installation", None, None, None)
    account = Account.objects.filter(uuid=scope.account_uuid).first()
    if account is None:
        raise AccountScopeError(_DENIED)
    if account.kind == Account.Kind.INDIVIDUAL:
        if scope.organization_uuid is not None or Organization.objects.filter(account=account).exists():
            raise AccountScopeError(_DENIED)
        return ResolvedResourceScope("account", account.id, None, None)
    organization_id = None
    workspace_id = None
    if scope.organization_uuid is not None:
        organization = Organization.objects.filter(uuid=scope.organization_uuid, account=account).first()
        if organization is None:
            raise AccountScopeError(_DENIED)
        organization_id = organization.id
        if scope.workspace_uuid is not None:
            workspace = Workspace.objects.filter(uuid=scope.workspace_uuid, organization=organization).first()
            if workspace is None or workspace.archived_at is not None:
                raise AccountScopeError(_DENIED)
            workspace_id = workspace.id
    return ResolvedResourceScope("account", account.id, organization_id, workspace_id)
