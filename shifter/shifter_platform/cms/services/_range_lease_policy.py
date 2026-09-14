"""Runtime Mission Control lease-policy resolution and administration (#2169)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.contrib.auth.models import Group
from django.db import connection, transaction
from pydantic import ValidationError

from cms.exceptions import CMSError
from cms.models import (
    MissionControlGroupLeasePolicy,
    MissionControlGroupLeasePolicyRevision,
    MissionControlTenantLeasePolicy,
    MissionControlTenantLeasePolicyRevision,
)
from shared.audit import AuditAction, AuditEntityType, AuditEvent, audit_log
from shared.auth import CTF_PARTICIPANT_GROUP
from shared.mission_control_lease import MissionControlLeasePolicy

if TYPE_CHECKING:
    from django.contrib.auth.models import User

# A transaction-scoped PostgreSQL mutex closes the absent-singleton race that a
# SELECT FOR UPDATE cannot cover. SQLite is used for fast behavioral tests and
# serializes writes itself; PostgreSQL concurrency coverage proves this mutex.
_ADVISORY_LOCK_NAMESPACE = 0x53484654
_ADVISORY_LOCK_KEY = 2169
_INELIGIBLE_GROUP_NAMES = frozenset({CTF_PARTICIPANT_GROUP})


class MissionControlLeasePolicyAdminError(CMSError):
    """Classified, bounded runtime lease-policy administration outcome."""

    class Kind(enum.Enum):
        FORBIDDEN = "forbidden"
        INVALID_POLICY = "invalid_policy"
        REVISION_CONFLICT = "revision_conflict"
        GROUP_NOT_FOUND = "group_not_found"
        GROUP_INELIGIBLE = "group_ineligible"
        CHILD_POLICY_CONFLICT = "child_policy_conflict"

    def __init__(self, kind: MissionControlLeasePolicyAdminError.Kind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class LeasePolicyAuditContext:
    """Trusted request attribution passed into strict-audited commands."""

    actor_type: str
    actor_id: int | None
    request_id: str = ""
    source_ip: str | None = None
    user_agent: str = ""


@dataclass(frozen=True, slots=True)
class LeasePolicyOverride:
    """One persisted complete policy and its compare-and-set revision."""

    policy: MissionControlLeasePolicy
    revision: int


@dataclass(frozen=True, slots=True)
class LeasePolicyGroupRevision:
    """Bounded group-policy provenance for an effective resolution."""

    group_id: int
    revision: int


@dataclass(frozen=True, slots=True)
class ResolvedMissionControlLeasePolicy:
    """Canonical policy plus bounded source/revision provenance."""

    policy: MissionControlLeasePolicy
    source: str
    tenant_revision: int
    group_revisions: tuple[LeasePolicyGroupRevision, ...]


@dataclass(frozen=True, slots=True)
class LeasePolicyGroupSettings:
    """Admin projection for one policy-eligible Django group."""

    group_id: int
    group_name: str
    revision: int
    override: LeasePolicyOverride | None


@dataclass(frozen=True, slots=True)
class MissionControlLeasePolicySettings:
    """Admin settings projection with visible fallback/override precedence."""

    baseline: MissionControlLeasePolicy
    tenant_revision: int
    tenant_override: LeasePolicyOverride | None
    effective_tenant: MissionControlLeasePolicy
    effective_source: str
    groups: tuple[LeasePolicyGroupSettings, ...]


def _error(kind: MissionControlLeasePolicyAdminError.Kind, message: str) -> MissionControlLeasePolicyAdminError:
    return MissionControlLeasePolicyAdminError(kind, message)


def _assert_active_superuser(actor: User) -> None:
    if not (
        getattr(actor, "is_authenticated", False)
        and getattr(actor, "is_active", False)
        and getattr(actor, "is_superuser", False)
    ):
        raise _error(
            MissionControlLeasePolicyAdminError.Kind.FORBIDDEN,
            "Only an active platform superuser may administer Mission Control lease policy",
        )


def _deployment_policy() -> MissionControlLeasePolicy:
    from django.conf import settings

    # Runtime composition already validates this value. Re-validating the object
    # preserves a fail-closed service boundary for test/alternate callers.
    return _canonical_policy(settings.MISSION_CONTROL_LEASE_POLICY)


def _canonical_policy(policy: object) -> MissionControlLeasePolicy:
    try:
        if isinstance(policy, MissionControlLeasePolicy):
            return MissionControlLeasePolicy.model_validate(policy.model_dump())
        return MissionControlLeasePolicy.model_validate(policy)
    except (TypeError, ValidationError) as exc:
        raise _error(
            MissionControlLeasePolicyAdminError.Kind.INVALID_POLICY,
            "Mission Control lease policy is invalid",
        ) from exc


def _policy_from_row(
    row: MissionControlTenantLeasePolicy | MissionControlGroupLeasePolicy,
) -> MissionControlLeasePolicy:
    return _canonical_policy(
        {
            "initial_days": row.initial_days,
            "extension_days": row.extension_days,
            "maximum_days": row.maximum_days,
            "extensions_enabled": row.extensions_enabled,
        }
    )


def _override_from_row(
    row: MissionControlTenantLeasePolicy | MissionControlGroupLeasePolicy,
) -> LeasePolicyOverride:
    return LeasePolicyOverride(policy=_policy_from_row(row), revision=row.revision)


def _policy_state(policy: MissionControlLeasePolicy, revision: int, **extra: object) -> dict[str, object]:
    return {
        **extra,
        **policy.model_dump(),
        "revision": revision,
    }


def _expected_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _error(
            MissionControlLeasePolicyAdminError.Kind.INVALID_POLICY,
            "Expected revision must be a non-negative integer",
        )
    return value


def _lock_policy_namespace() -> None:
    """Serialize authoritative policy reads/writes on PostgreSQL."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                [_ADVISORY_LOCK_NAMESPACE, _ADVISORY_LOCK_KEY],
            )


def _tenant_row(*, for_update: bool) -> MissionControlTenantLeasePolicy | None:
    rows = (
        MissionControlTenantLeasePolicy.objects.select_for_update()
        if for_update
        else MissionControlTenantLeasePolicy.objects.all()
    )
    return rows.filter(pk=1).first()


def _tenant_revision_row(*, for_update: bool) -> MissionControlTenantLeasePolicyRevision | None:
    rows = (
        MissionControlTenantLeasePolicyRevision.objects.select_for_update()
        if for_update
        else MissionControlTenantLeasePolicyRevision.objects.all()
    )
    return rows.filter(pk=1).first()


def _group_revision_row(
    group: Group,
    *,
    for_update: bool,
) -> MissionControlGroupLeasePolicyRevision | None:
    rows = (
        MissionControlGroupLeasePolicyRevision.objects.select_for_update()
        if for_update
        else MissionControlGroupLeasePolicyRevision.objects.all()
    )
    return rows.filter(group=group).first()


def _current_revision(
    revision_row: MissionControlTenantLeasePolicyRevision | MissionControlGroupLeasePolicyRevision | None,
    policy_row: MissionControlTenantLeasePolicy | MissionControlGroupLeasePolicy | None,
) -> int:
    if revision_row is not None:
        return revision_row.revision
    return policy_row.revision if policy_row is not None else 0


def _advance_revision(
    revision_row: MissionControlTenantLeasePolicyRevision | MissionControlGroupLeasePolicyRevision | None,
    *,
    actual: int,
    group: Group | None = None,
) -> int:
    next_revision = actual + 1
    if revision_row is None:
        if group is None:
            MissionControlTenantLeasePolicyRevision.objects.create(revision=next_revision)
        else:
            MissionControlGroupLeasePolicyRevision.objects.create(group=group, revision=next_revision)
    else:
        revision_row.revision = next_revision
        revision_row.save(update_fields=["revision", "updated_at"])
    return next_revision


def _effective_tenant(*, for_update: bool) -> tuple[MissionControlLeasePolicy, int, str]:
    row = _tenant_row(for_update=for_update)
    if row is None:
        return _deployment_policy(), 0, "deployment"
    return _policy_from_row(row), row.revision, "runtime"


def _matching_group_rows(user: User, *, for_update: bool) -> list[MissionControlGroupLeasePolicy]:
    user_id = getattr(user, "pk", None)
    if user_id is None:
        return []
    group_ids = list(user.groups.exclude(name__in=_INELIGIBLE_GROUP_NAMES).order_by("pk").values_list("pk", flat=True))
    rows = MissionControlGroupLeasePolicy.objects.filter(group_id__in=group_ids).select_related("group")
    if for_update:
        rows = rows.select_for_update()
    return list(rows.order_by("group_id"))


def resolve_mission_control_lease_policy(
    user: User,
    *,
    for_update: bool = False,
) -> ResolvedMissionControlLeasePolicy:
    """Resolve fallback -> tenant -> restrictive group policy for an authenticated owner."""
    if for_update:
        if not connection.in_atomic_block:
            raise RuntimeError("Locked lease-policy resolution requires an active transaction")
        _lock_policy_namespace()
    tenant, tenant_revision, tenant_source = _effective_tenant(for_update=for_update)
    rows = _matching_group_rows(user, for_update=for_update)
    if not rows:
        return ResolvedMissionControlLeasePolicy(tenant, tenant_source, tenant_revision, ())

    group_policies = [_policy_from_row(row) for row in rows]
    maximum_days = min(tenant.maximum_days, *(policy.maximum_days for policy in group_policies))
    initial_days = min(maximum_days, *(policy.initial_days for policy in group_policies))
    effective = _canonical_policy(
        {
            "initial_days": initial_days,
            "extension_days": min(policy.extension_days for policy in group_policies),
            "maximum_days": maximum_days,
            "extensions_enabled": tenant.extensions_enabled
            and all(policy.extensions_enabled for policy in group_policies),
        }
    )
    revisions = tuple(LeasePolicyGroupRevision(row.group_id, row.revision) for row in rows)
    return ResolvedMissionControlLeasePolicy(effective, "group", tenant_revision, revisions)


def get_mission_control_lease_settings(actor: User) -> MissionControlLeasePolicySettings:
    """Return the bounded settings projection for an active platform superuser."""
    _assert_active_superuser(actor)
    baseline = _deployment_policy()
    tenant_row = _tenant_row(for_update=False)
    tenant_revision_row = _tenant_revision_row(for_update=False)
    tenant_revision = _current_revision(tenant_revision_row, tenant_row)
    tenant_override = _override_from_row(tenant_row) if tenant_row is not None else None
    effective_tenant = tenant_override.policy if tenant_override else baseline
    policy_by_group = {
        row.group_id: row
        for row in MissionControlGroupLeasePolicy.objects.select_related("group").exclude(
            group__name__in=_INELIGIBLE_GROUP_NAMES
        )
    }
    revision_by_group = {
        row.group_id: row.revision
        for row in MissionControlGroupLeasePolicyRevision.objects.exclude(group__name__in=_INELIGIBLE_GROUP_NAMES)
    }
    groups = tuple(
        LeasePolicyGroupSettings(
            group_id=group.pk,
            group_name=group.name,
            revision=revision_by_group.get(
                group.pk,
                policy_by_group[group.pk].revision if group.pk in policy_by_group else 0,
            ),
            override=_override_from_row(policy_by_group[group.pk]) if group.pk in policy_by_group else None,
        )
        for group in Group.objects.exclude(name__in=_INELIGIBLE_GROUP_NAMES).order_by("name", "pk")
    )
    return MissionControlLeasePolicySettings(
        baseline=baseline,
        tenant_revision=tenant_revision,
        tenant_override=tenant_override,
        effective_tenant=effective_tenant,
        effective_source="runtime" if tenant_override else "deployment",
        groups=groups,
    )


def _validate_children(maximum_days: int, rows: list[MissionControlGroupLeasePolicy]) -> None:
    if any(row.initial_days > maximum_days or row.maximum_days > maximum_days for row in rows):
        raise _error(
            MissionControlLeasePolicyAdminError.Kind.CHILD_POLICY_CONFLICT,
            "A group lease policy exceeds the proposed tenant maximum",
        )


def _write_audit(
    *,
    entity_id: int,
    context: str,
    audit: LeasePolicyAuditContext,
    previous: dict[str, object] | None,
    current: dict[str, object],
) -> None:
    audit_log(
        AuditEvent(
            entity_type=AuditEntityType.CONFIG,
            entity_id=entity_id,
            action=AuditAction.UPDATE,
            actor_type=audit.actor_type,
            actor_id=audit.actor_id,
            previous_state=previous,
            new_state=current,
            context=context,
            source_ip=audit.source_ip,
            user_agent=audit.user_agent[:500],
            request_id=audit.request_id[:64],
        ),
        strict=True,
    )


def replace_tenant_lease_policy(
    actor: User,
    policy: MissionControlLeasePolicy,
    *,
    expected_revision: int,
    audit: LeasePolicyAuditContext,
) -> LeasePolicyOverride:
    """Create or replace the complete tenant override under revision CAS."""
    _assert_active_superuser(actor)
    canonical = _canonical_policy(policy)
    expected = _expected_revision(expected_revision)
    with transaction.atomic():
        _lock_policy_namespace()
        revision_row = _tenant_revision_row(for_update=True)
        row = _tenant_row(for_update=True)
        actual = _current_revision(revision_row, row)
        if expected != actual:
            raise _error(
                MissionControlLeasePolicyAdminError.Kind.REVISION_CONFLICT,
                "Mission Control lease policy changed; refresh and try again",
            )
        children = list(MissionControlGroupLeasePolicy.objects.select_for_update().order_by("group_id"))
        _validate_children(canonical.maximum_days, children)
        if row is not None and _policy_from_row(row) == canonical:
            return _override_from_row(row)
        previous = _policy_state(_policy_from_row(row), row.revision, scope="tenant") if row else None
        next_revision = _advance_revision(revision_row, actual=actual)
        if row is None:
            row = MissionControlTenantLeasePolicy.objects.create(
                **canonical.model_dump(),
                revision=next_revision,
            )
        else:
            for field, value in canonical.model_dump().items():
                setattr(row, field, value)
            row.revision = next_revision
            row.save(
                update_fields=[
                    "initial_days",
                    "extension_days",
                    "maximum_days",
                    "extensions_enabled",
                    "revision",
                    "updated_at",
                ]
            )
        _write_audit(
            entity_id=row.pk,
            context="mission_control_tenant_lease_policy",
            audit=audit,
            previous=previous,
            current=_policy_state(canonical, row.revision, scope="tenant"),
        )
        return _override_from_row(row)


def reset_tenant_lease_policy(
    actor: User,
    *,
    expected_revision: int,
    audit: LeasePolicyAuditContext,
) -> None:
    """Remove the tenant override after validating children against the live fallback."""
    _assert_active_superuser(actor)
    expected = _expected_revision(expected_revision)
    with transaction.atomic():
        _lock_policy_namespace()
        revision_row = _tenant_revision_row(for_update=True)
        row = _tenant_row(for_update=True)
        actual = _current_revision(revision_row, row)
        if expected != actual:
            raise _error(
                MissionControlLeasePolicyAdminError.Kind.REVISION_CONFLICT,
                "Mission Control lease policy changed; refresh and try again",
            )
        if row is None:
            return
        children = list(MissionControlGroupLeasePolicy.objects.select_for_update().order_by("group_id"))
        baseline = _deployment_policy()
        _validate_children(baseline.maximum_days, children)
        previous = _policy_state(_policy_from_row(row), row.revision, scope="tenant")
        next_revision = _advance_revision(revision_row, actual=actual)
        _write_audit(
            entity_id=row.pk,
            context="mission_control_tenant_lease_policy_reset",
            audit=audit,
            previous=previous,
            current={"scope": "tenant", "source": "deployment", "revision": next_revision},
        )
        row.delete()


def _eligible_group_for_update(group_id: int) -> Group:
    group = Group.objects.select_for_update().filter(pk=group_id).first()
    if group is None:
        raise _error(MissionControlLeasePolicyAdminError.Kind.GROUP_NOT_FOUND, "Group not found")
    if group.name in _INELIGIBLE_GROUP_NAMES:
        raise _error(
            MissionControlLeasePolicyAdminError.Kind.GROUP_INELIGIBLE,
            "This group is not eligible for Mission Control lease policy",
        )
    return group


def replace_group_lease_policy(
    actor: User,
    group_id: int,
    policy: MissionControlLeasePolicy,
    *,
    expected_revision: int,
    audit: LeasePolicyAuditContext,
) -> LeasePolicyOverride:
    """Create or replace one complete eligible-group policy under revision CAS."""
    _assert_active_superuser(actor)
    canonical = _canonical_policy(policy)
    expected = _expected_revision(expected_revision)
    with transaction.atomic():
        _lock_policy_namespace()
        tenant, _tenant_revision, _source = _effective_tenant(for_update=True)
        group = _eligible_group_for_update(group_id)
        if canonical.initial_days > tenant.maximum_days or canonical.maximum_days > tenant.maximum_days:
            raise _error(
                MissionControlLeasePolicyAdminError.Kind.INVALID_POLICY,
                "Group lease policy exceeds the effective tenant maximum",
            )
        revision_row = _group_revision_row(group, for_update=True)
        row = MissionControlGroupLeasePolicy.objects.select_for_update().filter(group=group).first()
        actual = _current_revision(revision_row, row)
        if expected != actual:
            raise _error(
                MissionControlLeasePolicyAdminError.Kind.REVISION_CONFLICT,
                "Mission Control group lease policy changed; refresh and try again",
            )
        if row is not None and _policy_from_row(row) == canonical:
            return _override_from_row(row)
        previous = _policy_state(_policy_from_row(row), row.revision, scope="group", group_id=group.pk) if row else None
        next_revision = _advance_revision(revision_row, actual=actual, group=group)
        if row is None:
            row = MissionControlGroupLeasePolicy.objects.create(
                group=group,
                **canonical.model_dump(),
                revision=next_revision,
            )
        else:
            for field, value in canonical.model_dump().items():
                setattr(row, field, value)
            row.revision = next_revision
            row.save(
                update_fields=[
                    "initial_days",
                    "extension_days",
                    "maximum_days",
                    "extensions_enabled",
                    "revision",
                    "updated_at",
                ]
            )
        _write_audit(
            entity_id=group.pk,
            context="mission_control_group_lease_policy",
            audit=audit,
            previous=previous,
            current=_policy_state(canonical, row.revision, scope="group", group_id=group.pk),
        )
        return _override_from_row(row)


def reset_group_lease_policy(
    actor: User,
    group_id: int,
    *,
    expected_revision: int,
    audit: LeasePolicyAuditContext,
) -> None:
    """Remove one eligible-group override under revision CAS."""
    _assert_active_superuser(actor)
    expected = _expected_revision(expected_revision)
    with transaction.atomic():
        _lock_policy_namespace()
        _effective_tenant(for_update=True)
        group = _eligible_group_for_update(group_id)
        revision_row = _group_revision_row(group, for_update=True)
        row = MissionControlGroupLeasePolicy.objects.select_for_update().filter(group=group).first()
        actual = _current_revision(revision_row, row)
        if expected != actual:
            raise _error(
                MissionControlLeasePolicyAdminError.Kind.REVISION_CONFLICT,
                "Mission Control group lease policy changed; refresh and try again",
            )
        if row is None:
            return
        previous = _policy_state(_policy_from_row(row), row.revision, scope="group", group_id=group.pk)
        next_revision = _advance_revision(revision_row, actual=actual, group=group)
        _write_audit(
            entity_id=group.pk,
            context="mission_control_group_lease_policy_reset",
            audit=audit,
            previous=previous,
            current={"scope": "group", "group_id": group.pk, "source": "tenant", "revision": next_revision},
        )
        row.delete()


__all__ = [
    "LeasePolicyAuditContext",
    "LeasePolicyGroupRevision",
    "LeasePolicyGroupSettings",
    "LeasePolicyOverride",
    "MissionControlLeasePolicyAdminError",
    "MissionControlLeasePolicySettings",
    "ResolvedMissionControlLeasePolicy",
    "get_mission_control_lease_settings",
    "replace_group_lease_policy",
    "replace_tenant_lease_policy",
    "reset_group_lease_policy",
    "reset_tenant_lease_policy",
    "resolve_mission_control_lease_policy",
]
