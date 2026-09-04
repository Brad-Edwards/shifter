"""Workspace resource-quota authoring, usage projection, and enforcement primitives.

Implements PLAT-239 (#1946) behind the ``workspaces.services`` facade, within the
boundaries fixed by ADR-046-R10 and
``docs/architecture/workspace-resource-quotas-preflight-1946.md``:

* **Policy authoring** (:func:`set_workspace_quota_policy`) is a superuser-only
  composition-root authority, never reachable through a workspace role — a quota is
  a platform guardrail owners/admins must not be able to raise or remove.
* **Usage read** (:func:`workspace_quota_usage`) is authorized by the existing
  ``READ_WORKSPACE`` role operation (owner/admin) and is strictly read-only.
* **Enforcement primitives** evaluate a resource under the *caller-held* workspace
  mutex, record every configured-policy decision as append-only evidence, and
  return a bounded :class:`QuotaVerdict`. A hard-cap rejection is signalled with
  :class:`WorkspaceQuotaRejected` *without* writing the rejection row, so the
  caller records the evidence after its transaction unwinds — a hard rejection
  must commit its decision without committing the denied action.

Missing policy means unlimited (compatibility); no decision is recorded for an
unlimited resource. Enforcement mode reuses ``shared.capacity.EnforcementMode``
terms only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from shared.audit import AuditAction, AuditEntityType, AuditEvent, audit_log
from workspaces.models import (
    QUOTA_MODE_ENFORCING,
    QUOTA_OUTCOME_ADMITTED,
    QUOTA_OUTCOME_REJECTED,
    QUOTA_OUTCOME_WARNED,
    QUOTA_RESOURCE_CONCURRENT_RANGES,
    QUOTA_RESOURCE_MEMBER_SEATS,
    WORKSPACE_QUOTA_MODE_VALUES,
    WORKSPACE_QUOTA_RESOURCE_VALUES,
    Workspace,
    WorkspaceMembership,
    WorkspaceQuotaDecision,
    WorkspaceQuotaPolicy,
    WorkspaceQuotaReservation,
)
from workspaces.roles import WorkspaceOperation

from ._authorization import authorize_workspace

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

#: Bounded number of recent decision rows the usage projection returns.
_RECENT_DECISION_LIMIT = 20
#: Correlation keys are stored in a bounded column; clip defensively.
_CORRELATION_KEY_MAX = 64


class WorkspaceQuotaError(Exception):
    """A safe, classified quota command outcome (validation/authority/not-found)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _error(code: str, message: str) -> WorkspaceQuotaError:
    """Build a classified quota command error."""
    return WorkspaceQuotaError(code, message)


@dataclass(frozen=True, slots=True)
class WorkspaceQuotaAuditContext:
    """Trusted attribution for a quota decision, supplied by the calling boundary.

    Matches the field shape of the other workspace audit contexts so a membership
    or lifecycle caller can forward its own attribution unchanged.
    """

    actor_type: str
    actor_id: int | None
    source_ip: str | None = None
    user_agent: str = ""
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class QuotaVerdict:
    """Bounded verdict of one quota evaluation. Scalars only."""

    resource: str
    outcome: str
    usage_before: int
    limit: int | None
    mode: str | None
    reason_code: str
    policy_revision: int = 0

    @property
    def rejected(self) -> bool:
        """Whether a hard cap rejected the action."""
        return self.outcome == QUOTA_OUTCOME_REJECTED

    @property
    def warned(self) -> bool:
        """Whether a soft cap warned (and admitted) the action."""
        return self.outcome == QUOTA_OUTCOME_WARNED

    @property
    def is_configured(self) -> bool:
        """Whether a policy was configured (an unlimited resource has none)."""
        return self.limit is not None


@dataclass(frozen=True, slots=True)
class WorkspaceResourceUsage:
    """Immutable per-resource usage-against-limit projection."""

    resource: str
    usage: int
    limit: int | None
    mode: str | None


@dataclass(frozen=True, slots=True)
class WorkspaceQuotaDecisionView:
    """Immutable projection of one recorded quota decision."""

    resource: str
    outcome: str
    limit: int
    mode: str
    usage_before: int
    requested_delta: int
    reason_code: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceQuotaProjection:
    """Immutable read-only quota surface: usage per resource + recent decisions."""

    workspace_uuid: UUID
    resources: tuple[WorkspaceResourceUsage, ...]
    recent_decisions: tuple[WorkspaceQuotaDecisionView, ...]


class WorkspaceQuotaRejected(Exception):
    """Internal control-flow: a hard cap rejected the action under the caller's lock.

    Carries the :class:`QuotaVerdict` so the caller records the rejection decision
    *after* its transaction unwinds. Never raise this inside a block that would
    roll the evidence back; the rejection row is written by
    :func:`record_workspace_quota_rejection` on the committed path.
    """

    def __init__(self, verdict: QuotaVerdict, workspace_id: int) -> None:
        super().__init__(verdict.reason_code)
        self.verdict = verdict
        self.workspace_id = workspace_id


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_resource(resource: object) -> str:
    """Validate a resource code against the closed vocabulary."""
    value = str(getattr(resource, "value", resource)).strip()
    if value not in WORKSPACE_QUOTA_RESOURCE_VALUES:
        raise _error(
            "quota_resource_invalid",
            "Quota resource must be one of: " + ", ".join(sorted(WORKSPACE_QUOTA_RESOURCE_VALUES)),
        )
    return value


def _validate_mode(mode: object) -> str:
    """Validate an enforcement mode against the closed vocabulary."""
    value = str(getattr(mode, "value", mode)).strip()
    if value not in WORKSPACE_QUOTA_MODE_VALUES:
        raise _error(
            "quota_mode_invalid",
            "Quota mode must be one of: " + ", ".join(sorted(WORKSPACE_QUOTA_MODE_VALUES)),
        )
    return value


def _validate_limit(limit: object) -> int:
    """Validate a non-negative integer limit (a bool is not an integer here)."""
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise _error("quota_limit_invalid", "Quota limit must be a non-negative integer")
    if limit < 0:
        raise _error("quota_limit_invalid", "Quota limit must be a non-negative integer")
    return limit


def _parse_workspace_uuid(workspace_uuid: str | UUID) -> UUID:
    """Parse a public workspace UUID, mapping a bad shape to a not-found outcome."""
    try:
        return workspace_uuid if isinstance(workspace_uuid, UUID) else UUID(str(workspace_uuid))
    except (AttributeError, TypeError, ValueError) as exc:
        raise _error("quota_workspace_not_found", "Workspace not found") from exc


# ---------------------------------------------------------------------------
# Usage + evaluation
# ---------------------------------------------------------------------------


def _lock_workspace_row(workspace_id: int) -> None:
    """Take the workspace row mutex so the usage count that follows is race-safe.

    Both production enforcement callers already hold this lock in the same
    transaction (membership add/accept and the CMS launch-admission seam), so this
    re-acquire is reentrant and free there; taking it here makes the primitive's
    race-safety contract self-enforcing rather than caller-dependent.
    """
    Workspace.objects.select_for_update().filter(pk=workspace_id).first()


def _current_usage(workspace_id: int, resource: str) -> int:
    """Return the current authoritative usage for a resource.

    Seat usage is the canonical membership count; concurrent-range usage is the
    number of *open* reservations. Callers of the enforcement primitives hold the
    workspace mutex, so this count is race-safe in those paths.
    """
    if resource == QUOTA_RESOURCE_MEMBER_SEATS:
        return WorkspaceMembership.objects.filter(workspace_id=workspace_id).count()
    if resource == QUOTA_RESOURCE_CONCURRENT_RANGES:
        return WorkspaceQuotaReservation.objects.filter(
            workspace_id=workspace_id,
            resource=QUOTA_RESOURCE_CONCURRENT_RANGES,
            released_at__isnull=True,
        ).count()
    raise _error("quota_resource_invalid", "Unknown quota resource")


def _policy_for(workspace_id: int, resource: str) -> WorkspaceQuotaPolicy | None:
    """Return the configured policy for a resource, or ``None`` (unlimited)."""
    return WorkspaceQuotaPolicy.objects.filter(workspace_id=workspace_id, resource=resource).first()


def _evaluate(resource: str, usage_before: int, policy: WorkspaceQuotaPolicy | None, *, delta: int = 1) -> QuotaVerdict:
    """Compute the bounded verdict for a resource given usage, policy, and delta."""
    if policy is None:
        return QuotaVerdict(resource, QUOTA_OUTCOME_ADMITTED, usage_before, None, None, "no_policy")
    if usage_before + delta <= policy.limit:
        return QuotaVerdict(
            resource, QUOTA_OUTCOME_ADMITTED, usage_before, policy.limit, policy.mode, "within_limit", policy.revision
        )
    if policy.mode == QUOTA_MODE_ENFORCING:
        return QuotaVerdict(
            resource,
            QUOTA_OUTCOME_REJECTED,
            usage_before,
            policy.limit,
            policy.mode,
            "hard_cap_exhausted",
            policy.revision,
        )
    return QuotaVerdict(
        resource, QUOTA_OUTCOME_WARNED, usage_before, policy.limit, policy.mode, "soft_cap_exceeded", policy.revision
    )


def _write_quota_audit(workspace_id: int, verdict: QuotaVerdict, audit: WorkspaceQuotaAuditContext) -> None:
    """Emit a strict shared-audit event for an applied (warned/rejected) limit."""
    audit_log(
        AuditEvent(
            entity_type=AuditEntityType.WORKSPACE,
            entity_id=workspace_id,
            action=AuditAction.QUOTA_APPLIED,
            actor_type=audit.actor_type or "system",
            actor_id=audit.actor_id,
            new_state={
                "resource": verdict.resource,
                "outcome": verdict.outcome,
                "limit": verdict.limit,
                "usage_before": verdict.usage_before,
                "mode": verdict.mode,
            },
            context="workspace_quota",
            source_ip=audit.source_ip,
            user_agent=audit.user_agent[:500],
            request_id=audit.request_id[:64],
        ),
        strict=True,
    )


def _record_decision(
    workspace_id: int,
    verdict: QuotaVerdict,
    correlation_key: str,
    audit: WorkspaceQuotaAuditContext,
    *,
    delta: int = 1,
) -> None:
    """Record one append-only decision row; project warnings/rejections to audit.

    Only configured-policy evaluations are recorded (an unlimited resource has no
    policy and no decision). ``limit`` and ``mode`` are set together, so the
    None-check narrows both for the persisted row.
    """
    if verdict.limit is None or verdict.mode is None:
        return
    WorkspaceQuotaDecision.objects.create(
        workspace_id=workspace_id,
        resource=verdict.resource,
        limit_at_decision=verdict.limit,
        mode_at_decision=verdict.mode,
        policy_revision=verdict.policy_revision,
        usage_before=verdict.usage_before,
        requested_delta=delta,
        outcome=verdict.outcome,
        reason_code=verdict.reason_code,
        actor_type=audit.actor_type or "",
        actor_id=audit.actor_id,
        correlation_key=(correlation_key or "")[:_CORRELATION_KEY_MAX],
    )
    if verdict.outcome in (QUOTA_OUTCOME_WARNED, QUOTA_OUTCOME_REJECTED):
        _write_quota_audit(workspace_id, verdict, audit)


# ---------------------------------------------------------------------------
# Enforcement primitives (caller holds the workspace mutex)
# ---------------------------------------------------------------------------


def admit_workspace_member_seat(
    workspace_id: int,
    audit: WorkspaceQuotaAuditContext,
    *,
    correlation_key: str = "",
) -> QuotaVerdict:
    """Evaluate the member-seat quota for adding one member (PLAT-239).

    MUST be called under the workspace mutex (both membership-add paths hold it).
    An admitted or warned decision is recorded inline, committed with the caller's
    membership insert. A hard cap raises :class:`WorkspaceQuotaRejected` without
    writing, so the caller records the rejection after its transaction unwinds and
    the membership is never created.
    """
    _lock_workspace_row(workspace_id)
    policy = _policy_for(workspace_id, QUOTA_RESOURCE_MEMBER_SEATS)
    usage = _current_usage(workspace_id, QUOTA_RESOURCE_MEMBER_SEATS)
    verdict = _evaluate(QUOTA_RESOURCE_MEMBER_SEATS, usage, policy)
    if verdict.rejected:
        raise WorkspaceQuotaRejected(verdict, workspace_id)
    _record_decision(workspace_id, verdict, correlation_key, audit)
    return verdict


def reserve_workspace_concurrent_range(
    workspace_id: int,
    correlation_key: str | UUID,
    audit: WorkspaceQuotaAuditContext,
) -> QuotaVerdict:
    """Reserve one concurrent-range slot under the workspace mutex (PLAT-239, ADR-046-R10).

    Idempotent on ``correlation_key``: a replay returns an admitted verdict without
    double-counting or re-deciding. An admitted or warned decision is recorded and
    the open reservation created inline, committed with the caller's CMS range
    reservation (so an active-range collision or any persistence failure rolls both
    back together). A hard cap raises :class:`WorkspaceQuotaRejected` without
    writing; the caller records the rejection after its transaction unwinds.
    """
    key = str(correlation_key)[:_CORRELATION_KEY_MAX]
    _lock_workspace_row(workspace_id)
    existing = WorkspaceQuotaReservation.objects.filter(
        workspace_id=workspace_id,
        resource=QUOTA_RESOURCE_CONCURRENT_RANGES,
        correlation_key=key,
    ).first()
    if existing is not None:
        usage = _current_usage(workspace_id, QUOTA_RESOURCE_CONCURRENT_RANGES)
        return QuotaVerdict(
            QUOTA_RESOURCE_CONCURRENT_RANGES, QUOTA_OUTCOME_ADMITTED, usage, None, None, "reservation_replay"
        )
    policy = _policy_for(workspace_id, QUOTA_RESOURCE_CONCURRENT_RANGES)
    usage = _current_usage(workspace_id, QUOTA_RESOURCE_CONCURRENT_RANGES)
    verdict = _evaluate(QUOTA_RESOURCE_CONCURRENT_RANGES, usage, policy)
    if verdict.rejected:
        raise WorkspaceQuotaRejected(verdict, workspace_id)
    WorkspaceQuotaReservation.objects.create(
        workspace_id=workspace_id,
        resource=QUOTA_RESOURCE_CONCURRENT_RANGES,
        correlation_key=key,
    )
    _record_decision(workspace_id, verdict, key, audit)
    return verdict


def release_workspace_concurrent_range(workspace_id: int, correlation_key: str | UUID) -> bool:
    """Idempotently release an open concurrent-range reservation on terminal convergence.

    A no-op when the reservation is absent or already released, so redelivery of a
    terminal range-status event and the ``reconcile_range_events`` backstop close
    the same reservation exactly once. Returns whether a row was released.
    """
    key = str(correlation_key)[:_CORRELATION_KEY_MAX]
    released = WorkspaceQuotaReservation.objects.filter(
        workspace_id=workspace_id,
        resource=QUOTA_RESOURCE_CONCURRENT_RANGES,
        correlation_key=key,
        released_at__isnull=True,
    ).update(released_at=timezone.now())
    return released > 0


def record_workspace_quota_rejection(
    workspace_id: int,
    verdict: QuotaVerdict,
    audit: WorkspaceQuotaAuditContext,
    *,
    correlation_key: str = "",
) -> None:
    """Record a hard-cap rejection decision in its own transaction.

    Called on the committed path after the denied action's transaction has unwound,
    so the rejection evidence survives while the denied mutation does not.
    """
    with transaction.atomic():
        _record_decision(workspace_id, verdict, correlation_key, audit)


# ---------------------------------------------------------------------------
# Policy authoring (superuser-only) and usage read (workspace role)
# ---------------------------------------------------------------------------


def _write_policy_audit(
    workspace: Workspace,
    policy: WorkspaceQuotaPolicy,
    audit: WorkspaceQuotaAuditContext,
    previous: dict[str, object] | None,
) -> None:
    """Strict-audit a quota policy change (bounded non-tenant facts only)."""
    audit_log(
        AuditEvent(
            entity_type=AuditEntityType.WORKSPACE,
            entity_id=workspace.pk,
            action=AuditAction.UPDATE,
            actor_type=audit.actor_type or "system",
            actor_id=audit.actor_id,
            previous_state=previous,
            new_state={
                "workspace_id": workspace.pk,
                "resource": policy.resource,
                "limit": policy.limit,
                "mode": policy.mode,
                "revision": policy.revision,
            },
            context="workspace_quota_policy",
            source_ip=audit.source_ip,
            user_agent=audit.user_agent[:500],
            request_id=audit.request_id[:64],
        ),
        strict=True,
    )


def set_workspace_quota_policy(
    actor: User,
    workspace_uuid: str | UUID,
    resource: str,
    limit: int,
    mode: str,
    *,
    audit: WorkspaceQuotaAuditContext,
) -> WorkspaceResourceUsage:
    """Author a workspace quota policy (superuser-only composition-root authority).

    A quota is a platform guardrail: authoring is authorized only by a superuser
    session and is never inferred from ``is_staff``, a workspace or organization
    role, a Django model permission, an API-token scope, or a provider claim. The
    policy is upserted under the workspace mutex, its ``revision`` bumped on change,
    and one strict ``shared.audit`` event written in the same transaction. A no-op
    (limit and mode unchanged) records no audit event.

    Raises:
        WorkspaceQuotaError: The actor is not a superuser, the workspace is not
            found, or the resource/mode/limit is invalid.
    """
    if not getattr(actor, "is_superuser", False):
        raise _error("quota_policy_forbidden", "Only a platform superuser may set workspace quota policy")
    resource_value = _validate_resource(resource)
    mode_value = _validate_mode(mode)
    limit_value = _validate_limit(limit)
    parsed = _parse_workspace_uuid(workspace_uuid)
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().filter(uuid=parsed).first()
        if workspace is None:
            raise _error("quota_workspace_not_found", "Workspace not found")
        policy = (
            WorkspaceQuotaPolicy.objects.select_for_update()
            .filter(workspace=workspace, resource=resource_value)
            .first()
        )
        if policy is None:
            policy = WorkspaceQuotaPolicy.objects.create(
                workspace=workspace,
                resource=resource_value,
                limit=limit_value,
                mode=mode_value,
                revision=1,
            )
            previous: dict[str, object] | None = None
        else:
            if policy.limit == limit_value and policy.mode == mode_value:
                return _usage_for(workspace.pk, resource_value, policy)
            previous = {"limit": policy.limit, "mode": policy.mode, "revision": policy.revision}
            policy.limit = limit_value
            policy.mode = mode_value
            policy.revision = policy.revision + 1
            policy.save(update_fields=["limit", "mode", "revision", "updated_at"])
        _write_policy_audit(workspace, policy, audit, previous)
        logger.info(
            "workspace quota policy set workspace_id=%s resource=%s limit=%s mode=%s actor_id=%s",
            workspace.pk,
            resource_value,
            limit_value,
            mode_value,
            getattr(actor, "pk", None),
        )
        return _usage_for(workspace.pk, resource_value, policy)


def _usage_for(workspace_id: int, resource: str, policy: WorkspaceQuotaPolicy | None) -> WorkspaceResourceUsage:
    """Build the usage-against-limit projection for a single resource."""
    return WorkspaceResourceUsage(
        resource=resource,
        usage=_current_usage(workspace_id, resource),
        limit=policy.limit if policy else None,
        mode=policy.mode if policy else None,
    )


def workspace_quota_usage(actor: User, workspace_uuid: str | UUID) -> WorkspaceQuotaProjection:
    """Return the read-only quota surface for a workspace (owner/admin authorized).

    Authorized by the existing ``READ_WORKSPACE`` role operation. Returns usage
    against the configured limit for every resource (an unconfigured resource
    reports ``limit=None`` — unlimited) plus a bounded list of recent decisions so
    an administrator can see when and why a cap applied. Strictly read-only.

    Raises:
        WorkspaceAuthorizationError: The actor may not read the workspace.
    """
    authorization = authorize_workspace(actor, workspace_uuid, WorkspaceOperation.READ_WORKSPACE)
    workspace_id = authorization.workspace_id
    policies = {p.resource: p for p in WorkspaceQuotaPolicy.objects.filter(workspace_id=workspace_id)}
    resources = tuple(
        _usage_for(workspace_id, resource, policies.get(resource))
        for resource in sorted(WORKSPACE_QUOTA_RESOURCE_VALUES)
    )
    decisions = tuple(
        WorkspaceQuotaDecisionView(
            resource=decision.resource,
            outcome=decision.outcome,
            limit=decision.limit_at_decision,
            mode=decision.mode_at_decision,
            usage_before=decision.usage_before,
            requested_delta=decision.requested_delta,
            reason_code=decision.reason_code,
            created_at=decision.created_at,
        )
        for decision in WorkspaceQuotaDecision.objects.filter(workspace_id=workspace_id).order_by("-created_at", "-id")[
            :_RECENT_DECISION_LIMIT
        ]
    )
    return WorkspaceQuotaProjection(
        workspace_uuid=authorization.workspace_uuid,
        resources=resources,
        recent_decisions=decisions,
    )
