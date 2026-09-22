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
    """Public projection of a durable customer account."""

    id: int
    uuid: UUID
    kind: str
    individual_principal_uuid: UUID | None


@dataclass(frozen=True, slots=True)
class OrganizationView:
    """Public projection of an account-owned organization."""

    id: int
    uuid: UUID
    account_id: int
    is_default: bool


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    """Public projection of an organization-owned workspace."""

    id: int
    uuid: UUID
    organization_id: int
    is_default: bool


@dataclass(frozen=True, slots=True)
class ResolvedResourceScope:
    """Resolved scalar ancestry for a validated resource scope."""

    kind: str
    account_id: int | None
    organization_id: int | None
    workspace_id: int | None


def _account_view(account: Account) -> AccountView:
    """Project an account model without exposing persistence internals."""
    return AccountView(account.id, account.uuid, account.kind, account.individual_principal_uuid)


def _organization_view(organization: Organization) -> OrganizationView:
    """Project an organization only when its account ancestry is present."""
    account_id = organization.account_id
    if account_id is None:
        raise AccountScopeError(_DENIED)
    return OrganizationView(organization.id, organization.uuid, account_id, organization.is_default)


def _workspace_view(workspace: Workspace) -> WorkspaceView:
    """Project a workspace without loading unrelated ancestry."""
    return WorkspaceView(workspace.id, workspace.uuid, workspace.organization_id, workspace.is_default)


def _locked_account(account_uuid: UUID) -> Account:
    """Resolve and lock one account by its durable public UUID."""
    account = Account.objects.select_for_update().filter(uuid=account_uuid).first()
    if account is None:
        raise AccountScopeError(_DENIED)
    return account


def _ensure_individual_defaults_locked(account: Account) -> None:
    """Reject subdivisions beneath an individual account."""
    if Organization.objects.filter(account=account).exists():
        raise AccountScopeError(_DENIED)


def _ensure_shared_defaults_locked(account: Account) -> None:
    """Create the required default organization and workspace when absent."""
    organization = Organization.objects.filter(account=account, is_default=True).first()
    if organization is None:
        organization = Organization.objects.create(account=account, name=_DEFAULT_NAME, is_default=True)
        _audit_create(AuditEntityType.ORGANIZATION, organization.pk, {"account_id": account.pk, "default": True})
    if not Workspace.objects.filter(organization=organization, is_default=True).exists():
        workspace = Workspace.objects.create(organization=organization, name=_DEFAULT_NAME, is_default=True)
        _audit_create(AuditEntityType.WORKSPACE, workspace.pk, {"organization_id": organization.pk, "default": True})


def _ensure_defaults_locked(account: Account) -> None:
    """Enforce the account-kind-specific default hierarchy under its lock."""
    if account.kind == Account.Kind.INDIVIDUAL:
        _ensure_individual_defaults_locked(account)
        return
    if account.kind in (Account.Kind.TEAM, Account.Kind.ENTERPRISE):
        _ensure_shared_defaults_locked(account)
        return
    raise AccountScopeError(_DENIED)


def _valid_account_declaration(kind: object, name: object, owner: PrincipalRef | None) -> bool:
    """Return whether one account creation request has a legal shape."""
    if kind not in Account.Kind.values or not isinstance(name, str) or not name.strip() or len(name) > 200:
        return False
    if kind == Account.Kind.INDIVIDUAL:
        return owner is not None and owner.kind == "human"
    return owner is None


def create_account(*, kind: str, name: str, owner: PrincipalRef | None = None) -> AccountView:
    """Create a typed account and its structural defaults without any grant."""
    if not _valid_account_declaration(kind, name, owner):
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


def _scope_account(scope: ResourceScope) -> Account:
    """Resolve the account root required by an account-scoped declaration."""
    account_uuid = scope.account_uuid
    if account_uuid is None:
        raise AccountScopeError(_DENIED)
    account = Account.objects.filter(uuid=account_uuid).first()
    if account is None:
        raise AccountScopeError(_DENIED)
    return account


def _resolve_individual_scope(account: Account, scope: ResourceScope) -> ResolvedResourceScope:
    """Resolve an individual account, which cannot own subdivisions."""
    if scope.organization_uuid is not None or Organization.objects.filter(account=account).exists():
        raise AccountScopeError(_DENIED)
    return ResolvedResourceScope("account", account.id, None, None)


def _resolve_nested_scope(account: Account, scope: ResourceScope) -> ResolvedResourceScope:
    """Resolve optional organization and workspace ancestry below a shared account."""
    if scope.organization_uuid is None:
        return ResolvedResourceScope("account", account.id, None, None)
    organization = Organization.objects.filter(uuid=scope.organization_uuid, account=account).first()
    if organization is None:
        raise AccountScopeError(_DENIED)
    if scope.workspace_uuid is None:
        return ResolvedResourceScope("account", account.id, organization.id, None)
    workspace = Workspace.objects.filter(uuid=scope.workspace_uuid, organization=organization).first()
    if workspace is None or workspace.archived_at is not None:
        raise AccountScopeError(_DENIED)
    return ResolvedResourceScope("account", account.id, organization.id, workspace.id)


def resolve_resource_scope(scope: ResourceScope) -> ResolvedResourceScope:
    """Validate account type and exact ancestry; never infer installation scope."""
    if not isinstance(scope, ResourceScope):
        raise AccountScopeError(_DENIED)
    if scope.kind == "installation":
        return ResolvedResourceScope("installation", None, None, None)
    account = _scope_account(scope)
    if account.kind == Account.Kind.INDIVIDUAL:
        return _resolve_individual_scope(account, scope)
    return _resolve_nested_scope(account, scope)
