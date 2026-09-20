"""Durable scoped OpenFGA policy mutations (ADR-066, #2315)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal, cast
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from shared.audit import AuditAction, AuditEntityType, AuditEvent, RequestAudit, audit_log
from shared.authorization import (
    ACTION_CATALOG,
    AdministrativeRoleChange,
    AuthorizationDecision,
    AuthorizationProvider,
    AuthorizationProviderError,
    AuthorizationRequest,
    CredentialCeiling,
    DecisionKind,
    GroupMembershipChange,
    PolicyEffect,
    PolicyRelationshipChange,
    RelationshipState,
    RelationshipSubject,
    RoleAssignmentChange,
    TargetRef,
    VersionedRelationshipChange,
    action_definition,
    predefined_policy_definition,
    resolve_authorization_descendants,
)
from shared.authorization.catalog import TargetType
from shared.authorization.relationships import SubjectKind
from shared.identity_scope import PrincipalRef, ResourceScope
from shared.principal_port import resolve_principal
from shared.principal_port import resolve_principal_uuid as directory_resolve_principal_uuid
from workspaces.models import (
    Account,
    AuthorizationGroup,
    AuthorizationMutationFence,
    AuthorizationOperation,
    AuthorizationPolicy,
    Organization,
    Workspace,
)

from ._account import resolve_resource_scope


class AuthorizationMutationConflict(RuntimeError):
    """A different or unresolved mutation owns the serialization fence."""


@dataclass(frozen=True, slots=True)
class PolicyMutationRequest:
    actor: PrincipalRef
    credential: CredentialCeiling
    subject: RelationshipSubject
    action: str
    target: TargetRef
    scope: ResourceScope
    effect: PolicyEffect
    idempotency_key: str
    model_id: str
    audit: RequestAudit = field(default_factory=RequestAudit)

    def __post_init__(self) -> None:
        PolicyRelationshipChange(self.subject, self.action, self.target, self.effect)
        if not isinstance(self.idempotency_key, str) or not 1 <= len(self.idempotency_key) <= 128:
            raise ValueError("idempotency key must contain 1 to 128 characters")
        if not isinstance(self.model_id, str) or not self.model_id:
            raise ValueError("authorization model id is required")
        if not isinstance(self.audit, RequestAudit):
            raise ValueError("trusted audit attribution is required")


@dataclass(frozen=True, slots=True)
class PolicyMutationResult:
    operation_id: UUID
    state: str


type NativeRelationshipChange = GroupMembershipChange | RoleAssignmentChange | AdministrativeRoleChange


@dataclass(frozen=True, slots=True)
class NativeRelationshipMutationRequest:
    """A native group membership or role assignment mutation."""

    actor: PrincipalRef
    credential: CredentialCeiling
    change: NativeRelationshipChange
    scope: ResourceScope
    idempotency_key: str
    model_id: str
    audit: RequestAudit = field(default_factory=RequestAudit)

    def __post_init__(self) -> None:
        if not isinstance(self.change, (GroupMembershipChange, RoleAssignmentChange, AdministrativeRoleChange)):
            raise ValueError("native relationship change is required")
        if not isinstance(self.idempotency_key, str) or not 1 <= len(self.idempotency_key) <= 128:
            raise ValueError("idempotency key must contain 1 to 128 characters")
        if not isinstance(self.model_id, str) or not self.model_id:
            raise ValueError("authorization model id is required")
        if not isinstance(self.audit, RequestAudit):
            raise ValueError("trusted audit attribution is required")


type MutationRequest = PolicyMutationRequest | NativeRelationshipMutationRequest

_RECONCILE_LEASE = timedelta(seconds=30)
_MAX_DELEGATION_DESCENDANTS = 1_000


def _hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _scope_payload(scope: ResourceScope) -> dict[str, object]:
    return {
        "kind": scope.kind,
        "account": str(scope.account_uuid) if scope.account_uuid else None,
        "organization": str(scope.organization_uuid) if scope.organization_uuid else None,
        "workspace": str(scope.workspace_uuid) if scope.workspace_uuid else None,
    }


def _request_payload(request: MutationRequest) -> dict[str, object]:
    common = {
        "actor": str(request.actor.uuid),
        "actor_kind": request.actor.kind,
        "scope": _scope_payload(request.scope),
        "model_id": request.model_id,
        "credential_actions": sorted(request.credential.actions),
    }
    if isinstance(request, PolicyMutationRequest):
        return {
            **common,
            "relationship_kind": AuthorizationOperation.RelationshipKind.ACTION,
            "subject_kind": request.subject.kind,
            "subject": str(request.subject.uuid),
            "action": request.action,
            "target_kind": request.target.type,
            "target": str(request.target.uuid) if request.target.uuid else None,
            "effect": request.effect.value,
        }
    change = request.change
    if isinstance(change, GroupMembershipChange):
        return {
            **common,
            "relationship_kind": AuthorizationOperation.RelationshipKind.GROUP_MEMBERSHIP,
            "subject_kind": "principal",
            "subject": str(change.principal_uuid),
            "action": "",
            "target_kind": "group",
            "target": str(change.group_uuid),
            "effect": change.effect.value,
        }
    if isinstance(change, AdministrativeRoleChange):
        return {
            **common,
            "relationship_kind": AuthorizationOperation.RelationshipKind.PREDEFINED_ROLE,
            "subject_kind": change.subject.kind,
            "subject": str(change.subject.uuid),
            "action": change.policy_code,
            "target_kind": change.target.type,
            "target": str(change.target.uuid) if change.target.uuid else None,
            "effect": change.effect.value,
        }
    return {
        **common,
        "relationship_kind": AuthorizationOperation.RelationshipKind.ROLE_ASSIGNMENT,
        "subject_kind": change.subject.kind,
        "subject": str(change.subject.uuid),
        "action": "",
        "target_kind": "role",
        "target": str(change.role_uuid),
        "effect": change.effect.value,
    }


def _idempotency_namespace(request: MutationRequest) -> str:
    return _hash(
        {
            "actor": str(request.actor.uuid),
            "actor_kind": request.actor.kind,
            "scope": _scope_payload(request.scope),
        }
    )


def _request_change(request: MutationRequest) -> PolicyRelationshipChange | NativeRelationshipChange:
    if isinstance(request, PolicyMutationRequest):
        return PolicyRelationshipChange(request.subject, request.action, request.target, request.effect)
    return request.change


def _fence_key(request: MutationRequest) -> str:
    change = _request_change(request)
    grant = change.grant_tuple
    return _hash({"user": grant.user, "relation": grant.relation, "object": grant.object})


def _operation_fields(request: MutationRequest) -> dict[str, object]:
    payload = _request_payload(request)
    return {
        "relationship_kind": payload["relationship_kind"],
        "subject_kind": payload["subject_kind"],
        "subject_uuid": payload["subject"],
        "action": payload["action"],
        "effect": payload["effect"],
        "target_kind": payload["target_kind"],
        "target_uuid": payload["target"],
    }


def _audit(operation: AuthorizationOperation, action: str, reason: str) -> None:
    audit_log(
        AuditEvent(
            entity_type=AuditEntityType.AUTHORIZATION_OPERATION,
            entity_id=0,
            entity_ref=str(operation.id),
            action=action,
            actor_type=operation.audit_actor_type or "system",
            actor_id=operation.audit_actor_id,
            new_state={
                "action": operation.action,
                "effect": operation.effect,
                "state": operation.state,
                "reason": reason,
                "target_kind": operation.target_kind,
            },
            context="authorization_policy",
            source_ip=operation.audit_source_ip,
            user_agent=operation.audit_user_agent,
            request_id=operation.audit_request_id,
        ),
        strict=True,
    )


def _operation_result(operation: AuthorizationOperation) -> PolicyMutationResult:
    return PolicyMutationResult(operation.id, operation.state)


def _reserve(
    request: MutationRequest,
    provider: AuthorizationProvider,
    *,
    state: AuthorizationOperation.State,
    reason: str,
    block: bool,
) -> tuple[AuthorizationOperation, bool]:
    digest = _hash(_request_payload(request))
    idempotency_namespace = _idempotency_namespace(request)
    fence_key = _fence_key(request)
    with transaction.atomic():
        AuthorizationMutationFence.objects.get_or_create(key=fence_key)
        fence = AuthorizationMutationFence.objects.select_for_update().get(key=fence_key)
        existing = AuthorizationOperation.objects.filter(
            idempotency_namespace=idempotency_namespace,
            idempotency_key=request.idempotency_key,
        ).first()
        if existing is not None:
            if existing.request_digest != digest:
                raise AuthorizationMutationConflict("idempotency key is bound to a different mutation")
            _assert_operation_provider(existing, provider)
            return existing, False
        if fence.blocked_operation_id is not None:
            raise AuthorizationMutationConflict("an unresolved authorization mutation blocks this edit")
        if block:
            fence.generation += 1
        operation_fields = _operation_fields(request)
        try:
            with transaction.atomic():
                operation = AuthorizationOperation.objects.create(
                    idempotency_key=request.idempotency_key,
                    idempotency_namespace=idempotency_namespace,
                    request_digest=digest,
                    fence_key=fence_key,
                    generation=fence.generation,
                    state=state,
                    actor_uuid=request.actor.uuid,
                    actor_kind=request.actor.kind,
                    audit_actor_type=request.audit.actor_type,
                    audit_actor_id=request.audit.actor_id,
                    audit_source_ip=request.audit.source_ip,
                    audit_user_agent=request.audit.user_agent,
                    audit_request_id=request.audit.request_id,
                    **operation_fields,
                    scope_kind=request.scope.kind,
                    account_uuid=request.scope.account_uuid,
                    organization_uuid=request.scope.organization_uuid,
                    workspace_uuid=request.scope.workspace_uuid,
                    model_id=request.model_id,
                    provider_store_id=provider.store_id,
                    credential_digest=_hash(
                        {
                            "actions": sorted(request.credential.actions),
                            "actor_type": request.audit.actor_type,
                            "actor_id": request.audit.actor_id,
                        }
                    ),
                    outcome_reason=reason,
                    reconcile_lease_until=timezone.now() + _RECONCILE_LEASE if block else None,
                )
        except IntegrityError as exc:
            winner = AuthorizationOperation.objects.filter(
                idempotency_namespace=idempotency_namespace,
                idempotency_key=request.idempotency_key,
            ).first()
            if winner is None:
                raise
            if winner.request_digest != digest:
                raise AuthorizationMutationConflict("idempotency key is bound to a different mutation") from exc
            _assert_operation_provider(winner, provider)
            return winner, False
        if block:
            fence.blocked_operation_id = operation.id
        fence.save(update_fields=["generation", "blocked_operation_id", "updated_at"])
        audit_action = {
            AuthorizationOperation.State.REQUESTED: AuditAction.AUTHORIZATION_REQUESTED,
            AuthorizationOperation.State.DENIED: AuditAction.AUTHORIZATION_DENIED,
        }[state]
        _audit(operation, audit_action, reason)
        return operation, True


def _set_outcome(
    owner: AuthorizationOperation, state: AuthorizationOperation.State, reason: str
) -> AuthorizationOperation:
    with transaction.atomic():
        operation = AuthorizationOperation.objects.select_for_update().get(pk=owner.id)
        if operation.state in {AuthorizationOperation.State.CONFIRMED, AuthorizationOperation.State.DENIED}:
            return operation
        if operation.reconcile_lease_until != owner.reconcile_lease_until:
            return operation
        operation.state = state
        operation.outcome_reason = reason
        operation.reconcile_lease_until = None
        operation.save(update_fields=["state", "outcome_reason", "reconcile_lease_until", "updated_at"])
        fence = AuthorizationMutationFence.objects.select_for_update().get(key=operation.fence_key)
        if (
            state in {AuthorizationOperation.State.CONFIRMED, AuthorizationOperation.State.DENIED}
            and fence.blocked_operation_id == operation.id
        ):
            fence.blocked_operation_id = None
            fence.save(update_fields=["blocked_operation_id", "updated_at"])
        audit_action = {
            AuthorizationOperation.State.CONFIRMED: AuditAction.AUTHORIZATION_CONFIRMED,
            AuthorizationOperation.State.DENIED: AuditAction.AUTHORIZATION_DENIED,
            AuthorizationOperation.State.UNRESOLVED: AuditAction.AUTHORIZATION_UNRESOLVED,
        }[state]
        _audit(operation, audit_action, reason)
        return operation


def _clear_reconcile_lease(owner: AuthorizationOperation) -> AuthorizationOperation:
    with transaction.atomic():
        operation = AuthorizationOperation.objects.select_for_update().get(pk=owner.id)
        if operation.reconcile_lease_until != owner.reconcile_lease_until:
            return operation
        operation.reconcile_lease_until = None
        operation.save(update_fields=["reconcile_lease_until", "updated_at"])
        return operation


def _management_action(target_type: str) -> str:
    return {
        "installation": "installation.manage_authorization",
        "account": "account.manage_authorization",
        "organization": "organization.manage_authorization",
        "workspace": "workspace.manage_authorization",
        "event": "event.manage",
        "range": "range.manage",
    }[target_type]


def _delegation_requests(request: PolicyMutationRequest) -> tuple[AuthorizationRequest, AuthorizationRequest]:
    if not action_definition(request.action).delegable:
        raise AuthorizationMutationConflict("authorization action cannot be delegated")
    return (
        AuthorizationRequest(request.actor, request.action, request.target, request.scope, request.credential),
        AuthorizationRequest(
            request.actor,
            _management_action(request.target.type),
            request.target,
            request.scope,
            request.credential,
        ),
    )


def _scope_target(scope: ResourceScope) -> TargetRef:
    if scope.workspace_uuid is not None:
        return TargetRef("workspace", scope.workspace_uuid)
    if scope.organization_uuid is not None:
        return TargetRef("organization", scope.organization_uuid)
    if scope.account_uuid is not None:
        return TargetRef("account", scope.account_uuid)
    return TargetRef("installation")


def _bounded_rows(query, remaining: int):
    rows = tuple(query.order_by("uuid")[: remaining + 1])
    if len(rows) > remaining:
        raise AuthorizationMutationConflict("authorization descendant limit exceeded")
    return rows


def _authoritative_descendant_targets(target: TargetRef) -> tuple[tuple[TargetRef, ResourceScope], ...]:
    """Resolve a bounded complete descendant set from SQL-owning domains."""
    if target.type in {"event", "range"}:
        return ()

    account_query = Account.objects.none()
    organization_query = Organization.objects.none()
    workspace_query = Workspace.objects.none()
    if target.type == "installation":
        account_query = Account.objects.all()
        organization_query = Organization.objects.filter(account__isnull=False)
        workspace_query = Workspace.objects.filter(organization__account__isnull=False)
    elif target.uuid is None:
        raise AuthorizationMutationConflict("authorization target is unavailable")
    elif target.type == "account":
        organization_query = Organization.objects.filter(account__uuid=target.uuid)
        workspace_query = Workspace.objects.filter(organization__account__uuid=target.uuid)
    elif target.type == "organization":
        workspace_query = Workspace.objects.filter(organization__uuid=target.uuid)
    elif target.type == "workspace":
        workspace_query = Workspace.objects.filter(uuid=target.uuid)

    remaining = _MAX_DELEGATION_DESCENDANTS
    descendants: list[tuple[TargetRef, ResourceScope]] = []
    if target.type == "installation":
        accounts = _bounded_rows(account_query, remaining)
        descendants.extend((TargetRef("account", item.uuid), ResourceScope("account", item.uuid)) for item in accounts)
        remaining -= len(accounts)
    if target.type in {"installation", "account"}:
        organizations = _bounded_rows(organization_query.select_related("account"), remaining)
        descendants.extend(
            (TargetRef("organization", item.uuid), ResourceScope("account", item.account.uuid, item.uuid))
            for item in organizations
        )
        remaining -= len(organizations)
    workspaces = _bounded_rows(workspace_query.select_related("organization__account"), remaining)
    for workspace in workspaces:
        scope = ResourceScope(
            "account", workspace.organization.account.uuid, workspace.organization.uuid, workspace.uuid
        )
        if target.type != "workspace":
            descendants.append((TargetRef("workspace", workspace.uuid), scope))
    remaining = _MAX_DELEGATION_DESCENDANTS - len(descendants)
    for workspace in workspaces:
        scope = ResourceScope(
            "account", workspace.organization.account.uuid, workspace.organization.uuid, workspace.uuid
        )
        external = resolve_authorization_descendants((workspace.pk,), remaining)
        descendants.extend((item, scope) for item in external)
        remaining -= len(external)
    return tuple(descendants)


def _native_delegation_requests(request: NativeRelationshipMutationRequest) -> tuple[AuthorizationRequest, ...]:
    """Require complete administrative authority at the native object's exact scope.

    Native membership can amplify every role currently or subsequently assigned
    to the group. Requiring the full delegable set at and below that scope is
    intentionally stronger than a single manage-members check and prevents a
    partial administrator from using membership as a privilege-escalation path.
    """
    if isinstance(request.change, AdministrativeRoleChange):
        definition = predefined_policy_definition(request.change.policy_code)
        target = request.change.target
        actions = definition.actions
    elif isinstance(request.change, GroupMembershipChange):
        target = _scope_target(request.scope)
        hierarchy = ("installation", "account", "organization", "workspace", "event", "range")
        root_index = hierarchy.index(target.type)
        actions = frozenset(
            item.code
            for item in ACTION_CATALOG
            if hierarchy.index(item.target_type) >= root_index and (item.delegable or item.requires_administrator)
        )
    else:
        target = _scope_target(request.scope)
        actions = frozenset(item.code for item in ACTION_CATALOG if item.target_type == target.type)
    if not actions.issubset(request.credential.actions):
        return ()

    action_definitions = tuple(action_definition(action) for action in sorted(actions))
    needs_descendants = any(item.target_type != target.type for item in action_definitions)
    concrete_targets: tuple[tuple[TargetRef, ResourceScope], ...] = ((target, request.scope),)
    if needs_descendants:
        concrete_targets += _authoritative_descendant_targets(target)
    return tuple(
        AuthorizationRequest(request.actor, definition.code, concrete_target, concrete_scope, request.credential)
        for definition in action_definitions
        for concrete_target, concrete_scope in concrete_targets
        if concrete_target.type == definition.target_type
    )


def _scope_lookup(scope: ResourceScope) -> dict[str, object]:
    return {
        "scope_kind": scope.kind,
        "account_uuid": scope.account_uuid,
        "organization_uuid": scope.organization_uuid,
        "workspace_uuid": scope.workspace_uuid,
        "is_active": True,
    }


def _resolve_principal_uuid(principal_uuid: UUID) -> None:
    directory_resolve_principal_uuid(principal_uuid)


def _resolve_concrete_target(target: TargetRef, scope: ResourceScope) -> None:
    """Verify leaf ownership in SQL before trusting a provider decision."""
    if target.type not in {"event", "range"}:
        return
    resolved = resolve_resource_scope(scope)
    if resolved.workspace_id is None or target not in resolve_authorization_descendants(
        (resolved.workspace_id,), _MAX_DELEGATION_DESCENDANTS
    ):
        raise AuthorizationMutationConflict("authorization target is unavailable")


def _resolve_native_change(request: NativeRelationshipMutationRequest) -> None:
    """Re-resolve durable identities and exact SQL-owned metadata ancestry."""
    resolve_principal(request.actor)
    change = request.change
    if isinstance(change, GroupMembershipChange):
        _resolve_principal_uuid(change.principal_uuid)
        if not AuthorizationGroup.objects.filter(uuid=change.group_uuid, **_scope_lookup(request.scope)).exists():
            raise AuthorizationMutationConflict("authorization group is unavailable")
        if change.effect == PolicyEffect.GRANT and change.principal_uuid == request.actor.uuid:
            raise AuthorizationMutationConflict("self-assignment is not permitted")
        return
    if isinstance(change, AdministrativeRoleChange):
        # Constructing each request validates the exact target against the
        # SQL-resolved ancestry before any provider write is attempted.
        predefined_policy_definition(change.policy_code)
        _resolve_concrete_target(change.target, request.scope)
        if change.subject.kind == "principal":
            _resolve_principal_uuid(change.subject.uuid)
            if change.effect == PolicyEffect.GRANT and change.subject.uuid == request.actor.uuid:
                raise AuthorizationMutationConflict("self-assignment is not permitted")
        elif change.subject.kind == "group":
            if not AuthorizationGroup.objects.filter(uuid=change.subject.uuid, **_scope_lookup(request.scope)).exists():
                raise AuthorizationMutationConflict("authorization subject is unavailable")
        return
    if change.subject.kind == "principal":
        _resolve_principal_uuid(change.subject.uuid)
        if change.effect == PolicyEffect.GRANT and change.subject.uuid == request.actor.uuid:
            raise AuthorizationMutationConflict("self-assignment is not permitted")
    elif not AuthorizationGroup.objects.filter(uuid=change.subject.uuid, **_scope_lookup(request.scope)).exists():
        raise AuthorizationMutationConflict("authorization subject is unavailable")
    if not AuthorizationPolicy.objects.filter(
        uuid=change.role_uuid,
        predefined_code="",
        **_scope_lookup(request.scope),
    ).exists():
        raise AuthorizationMutationConflict("authorization policy is unavailable")


def _resolve_policy_change(request: PolicyMutationRequest) -> None:
    resolve_principal(request.actor)
    _resolve_concrete_target(request.target, request.scope)
    if request.subject.kind == "principal":
        _resolve_principal_uuid(request.subject.uuid)
        if request.effect == PolicyEffect.GRANT and request.subject.uuid == request.actor.uuid:
            raise AuthorizationMutationConflict("self-assignment is not permitted")
    elif request.subject.kind == "group":
        if not AuthorizationGroup.objects.filter(uuid=request.subject.uuid, **_scope_lookup(request.scope)).exists():
            raise AuthorizationMutationConflict("authorization subject is unavailable")
    elif not AuthorizationPolicy.objects.filter(
        uuid=request.subject.uuid,
        predefined_code="",
        **_scope_lookup(request.scope),
    ).exists():
        raise AuthorizationMutationConflict("authorization subject is unavailable")


def _matches(effect: str, state: RelationshipState) -> bool:
    if effect == PolicyEffect.GRANT:
        return bool(state.grant_present and not state.deny_present)
    return bool(state.deny_present and not state.grant_present)


def apply_policy_mutation(request: PolicyMutationRequest, provider: AuthorizationProvider) -> PolicyMutationResult:
    """Authorize, journal, apply and read back one serialized tuple change."""
    resolve_resource_scope(request.scope)
    _assert_request_provider(request, provider)
    try:
        _resolve_policy_change(request)
        decisions = provider.batch_check(_delegation_requests(request))
    except Exception:
        decisions = (AuthorizationDecision(DecisionKind.EVALUATOR_ERROR, "evaluator_unavailable"),)
    if len(decisions) != 2 or not all(decision.allowed for decision in decisions):
        operation, _ = _reserve(
            request,
            provider,
            state=AuthorizationOperation.State.DENIED,
            reason="delegation_denied",
            block=False,
        )
        return _operation_result(operation)

    operation, created = _reserve(
        request,
        provider,
        state=AuthorizationOperation.State.REQUESTED,
        reason="accepted",
        block=True,
    )
    if not created:
        if operation.state in {AuthorizationOperation.State.REQUESTED, AuthorizationOperation.State.UNRESOLVED}:
            return reconcile_policy_mutation(operation.id, provider)
        return _operation_result(operation)
    if operation.state != AuthorizationOperation.State.REQUESTED:
        return _operation_result(operation)
    change = _change_from_operation(operation)
    try:
        provider.write_relationships(change)
        observed = provider.read_relationships(change)
    except AuthorizationProviderError:
        return _operation_result(
            _set_outcome(operation, AuthorizationOperation.State.UNRESOLVED, "provider_outcome_unknown")
        )
    if not _matches(request.effect, observed):
        return _operation_result(_set_outcome(operation, AuthorizationOperation.State.UNRESOLVED, "readback_mismatch"))
    return _operation_result(_set_outcome(operation, AuthorizationOperation.State.CONFIRMED, "readback_confirmed"))


def apply_native_relationship_mutation(
    request: NativeRelationshipMutationRequest,
    provider: AuthorizationProvider,
) -> PolicyMutationResult:
    """Apply a serialized native group membership or role assignment."""
    resolve_resource_scope(request.scope)
    _assert_request_provider(request, provider)
    try:
        _resolve_native_change(request)
        checks = _native_delegation_requests(request)
        decisions = provider.batch_check(checks)
        allowed = bool(checks) and len(decisions) == len(checks) and all(item.allowed for item in decisions)
    except Exception:
        allowed = False
    if not allowed:
        operation, _ = _reserve(
            request,
            provider,
            state=AuthorizationOperation.State.DENIED,
            reason="delegation_denied",
            block=False,
        )
        return _operation_result(operation)
    operation, created = _reserve(
        request,
        provider,
        state=AuthorizationOperation.State.REQUESTED,
        reason="accepted",
        block=True,
    )
    if not created:
        if operation.state in {AuthorizationOperation.State.REQUESTED, AuthorizationOperation.State.UNRESOLVED}:
            return reconcile_policy_mutation(operation.id, provider)
        return _operation_result(operation)
    if operation.state != AuthorizationOperation.State.REQUESTED:
        return _operation_result(operation)
    try:
        change = _change_from_operation(operation)
        provider.write_relationships(change)
        observed = provider.read_relationships(change)
    except AuthorizationProviderError:
        return _operation_result(
            _set_outcome(operation, AuthorizationOperation.State.UNRESOLVED, "provider_outcome_unknown")
        )
    if not _matches(request.change.effect, observed):
        return _operation_result(_set_outcome(operation, AuthorizationOperation.State.UNRESOLVED, "readback_mismatch"))
    return _operation_result(_set_outcome(operation, AuthorizationOperation.State.CONFIRMED, "readback_confirmed"))


def _base_change_from_operation(
    operation: AuthorizationOperation,
) -> PolicyRelationshipChange | NativeRelationshipChange:
    effect = PolicyEffect(operation.effect)
    subject_uuid = UUID(str(operation.subject_uuid))
    target_uuid = UUID(str(operation.target_uuid)) if operation.target_uuid is not None else None
    if operation.relationship_kind == AuthorizationOperation.RelationshipKind.GROUP_MEMBERSHIP:
        if target_uuid is None:
            raise AuthorizationMutationConflict("authorization operation is invalid")
        return GroupMembershipChange(target_uuid, subject_uuid, effect)
    if operation.relationship_kind == AuthorizationOperation.RelationshipKind.ROLE_ASSIGNMENT:
        if target_uuid is None:
            raise AuthorizationMutationConflict("authorization operation is invalid")
        return RoleAssignmentChange(
            target_uuid,
            RelationshipSubject(cast(SubjectKind, operation.subject_kind), subject_uuid),
            effect,
        )
    if operation.relationship_kind == AuthorizationOperation.RelationshipKind.PREDEFINED_ROLE:
        return AdministrativeRoleChange(
            RelationshipSubject(cast(SubjectKind, operation.subject_kind), subject_uuid),
            operation.action,
            TargetRef(cast(TargetType, operation.target_kind), target_uuid),
            effect,
        )
    return PolicyRelationshipChange(
        RelationshipSubject(cast(SubjectKind, operation.subject_kind), subject_uuid),
        operation.action,
        TargetRef(cast(TargetType, operation.target_kind), target_uuid),
        effect,
    )


def _change_from_operation(operation: AuthorizationOperation) -> VersionedRelationshipChange:
    return VersionedRelationshipChange(
        _base_change_from_operation(operation),
        operation.fence_key,
        operation.generation,
    )


def _assert_request_provider(request: MutationRequest, provider: AuthorizationProvider) -> None:
    if not provider.store_id or provider.model_id != request.model_id:
        raise AuthorizationMutationConflict("authorization provider binding changed")


def _assert_operation_provider(operation: AuthorizationOperation, provider: AuthorizationProvider) -> None:
    if not operation.provider_store_id or (
        operation.provider_store_id != provider.store_id or operation.model_id != provider.model_id
    ):
        raise AuthorizationMutationConflict("authorization provider binding changed")


def authorize_operation_reconciliation(
    operation_id: UUID,
    actor: PrincipalRef,
    credential: CredentialCeiling,
    provider: AuthorizationProvider,
) -> None:
    """Require current caller authority for every effect a recovery can replay."""
    operation = AuthorizationOperation.objects.get(pk=operation_id)
    _assert_operation_provider(operation, provider)
    scope = ResourceScope(
        cast(Literal["installation", "account"], operation.scope_kind),
        operation.account_uuid,
        operation.organization_uuid,
        operation.workspace_uuid,
    )
    resolve_resource_scope(scope)
    change = _base_change_from_operation(operation)
    checks: tuple[AuthorizationRequest, ...]
    if isinstance(change, PolicyRelationshipChange):
        request = PolicyMutationRequest(
            actor=actor,
            credential=credential,
            scope=scope,
            model_id=operation.model_id,
            idempotency_key=operation.idempotency_key,
            subject=change.subject,
            action=change.action,
            target=change.target,
            effect=change.effect,
        )
        _resolve_policy_change(request)
        checks = _delegation_requests(request)
    else:
        native_request = NativeRelationshipMutationRequest(
            actor=actor,
            credential=credential,
            scope=scope,
            model_id=operation.model_id,
            idempotency_key=operation.idempotency_key,
            change=change,
        )
        _resolve_native_change(native_request)
        checks = _native_delegation_requests(native_request)
    decisions = provider.batch_check(checks)
    if not checks or len(decisions) != len(checks) or not all(item.allowed for item in decisions):
        raise AuthorizationMutationConflict("authorization recovery denied")


def reconcile_policy_mutation(operation_id: UUID, provider: AuthorizationProvider) -> PolicyMutationResult:
    """Lease and recover a requested/ambiguous write using idempotent tuples."""
    with transaction.atomic():
        operation = AuthorizationOperation.objects.select_for_update().get(pk=operation_id)
        if operation.state not in {AuthorizationOperation.State.REQUESTED, AuthorizationOperation.State.UNRESOLVED}:
            return _operation_result(operation)
        _assert_operation_provider(operation, provider)
        now = timezone.now()
        if operation.reconcile_lease_until is not None and operation.reconcile_lease_until > now:
            return _operation_result(operation)
        operation.reconcile_lease_until = now + _RECONCILE_LEASE
        operation.save(update_fields=["reconcile_lease_until", "updated_at"])

    change = _change_from_operation(operation)
    try:
        observed = provider.read_relationships(change)
        if not _matches(operation.effect, observed):
            provider.write_relationships(change)
            observed = provider.read_relationships(change)
    except AuthorizationProviderError:
        if operation.state == AuthorizationOperation.State.REQUESTED:
            return _operation_result(
                _set_outcome(operation, AuthorizationOperation.State.UNRESOLVED, "provider_outcome_unknown")
            )
        return _operation_result(_clear_reconcile_lease(operation))
    if not _matches(operation.effect, observed):
        return _operation_result(_clear_reconcile_lease(operation))
    return _operation_result(_set_outcome(operation, AuthorizationOperation.State.CONFIRMED, "reconciled_primary_read"))
