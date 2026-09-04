"""CMS warm-pool claim path for initial launches (#28).

Inserted into the canonical RAES launch orchestration *after* every product,
tenancy, backend, and capacity gate and the reauthorized workspace lock, and
*before* the cold create branch. A miss, race, disabled policy, or unsupported
backend cold-falls-back with the inputs already validated for that launch -- no
divergent revalidation (preflight #28).

On a hit the claim is atomic (``engine.services.claim_ready_generation``), the
system-owned generation's ownership and workspace scope are transferred to the
claimant through the audited ``reassign_range_owner(..., rehome=True)`` facade
(the same handover the CTF spare path uses -- shared low-level mechanism, not the
CTF recovery policy), and only then is the durable ``activate`` operation enqueued.
The provisioner's activation rotates/scrubs every pre-claim credential and access
surface and creates the claimant's fresh access; nothing user-specific is reused.

Compatibility is decided by the canonical digest (:mod:`shared.warm_pool.compatibility`):
the launch computes the digest of its immutable inputs and claims a ready
generation whose digest is equal. The digest excludes ``user_id`` -- warm
eligibility is the ownership-neutral RAES realization identity (registered
``package_digest`` + ``lock_digest``, #1607).
"""

from __future__ import annotations

import logging
from uuid import UUID

from django.contrib.auth.models import User
from django.db import transaction

from shared.enums import RangeSource
from shared.range_instantiation_policy import InstantiationPurpose, backend_supports_warm_activation
from shared.warm_pool.compatibility import CompatibilityKey, compatibility_digest
from shared.warm_pool.policy import (
    WarmPoolOverride,
    WarmPoolRuntimePolicy,
    resolve_effective_policy,
)

logger = logging.getLogger(__name__)

# Reconciler assumptions the warm-prepare side stamps its generations with; the
# launch side must derive the same values for a claim to match. Warm v1 serves
# personal-workspace Mission Control live-fire launches; any other launch computes
# a different digest and safely cold-falls-back.
ISOLATION_PERSONAL = "personal"
ISOLATION_SHARED = "shared"


def warm_isolation_class(user: User, workspace_id: int) -> str:
    """Classify the launch workspace for the compatibility digest: personal vs shared.

    Goes through the workspaces service seam (ADR-001): the launch workspace is
    personal iff it is the user's own personal workspace. Warm v1 prepares
    generations for the personal-workspace class, so a shared/org-workspace launch
    computes a different digest and cold-falls-back.
    """
    from workspaces.services import resolve_personal_workspace

    personal = resolve_personal_workspace(user)
    return ISOLATION_PERSONAL if personal.workspace_id == workspace_id else ISOLATION_SHARED


def build_compatibility_key(
    *,
    backend: str,
    instantiation_purpose: str,
    range_source: str,
    workspace_isolation_class: str,
    egress_mode: str,
    scenario: str,
    package_digest: str,
    lock_digest: str,
) -> CompatibilityKey:
    """Assemble a :class:`CompatibilityKey` from launch-resolved realization + posture.

    The single builder both the launch claim path and the warm reconciler call, so
    the two sides derive an identical digest by construction. No bucket-declared
    routing (partition/placement/bucket id) is echoed here -- those are enforced
    separately by the claim's effective-policy bucket-set filter, keeping the digest
    a proof of realization identity resolved independently on each side.
    """
    return CompatibilityKey(
        backend=backend,
        instantiation_purpose=instantiation_purpose,
        range_source=range_source,
        workspace_isolation_class=workspace_isolation_class,
        egress_mode=egress_mode,
        scenario=scenario,
        package_digest=package_digest,
        lock_digest=lock_digest,
    )


def _eligible_buckets(policy: WarmPoolRuntimePolicy, *, backend: str, scenario: str):
    """Return the effective-policy buckets that warm this backend+scenario.

    These are the only buckets a launch may claim from; an override that disables or
    narrows the bucket set removes candidates here, and the claim is filtered to
    exactly these bucket ids so a launch can never receive a generation from a
    bucket the current server-side (deployment + override) policy no longer
    authorizes (security: policy-to-allocation binding).
    """
    return [b for b in policy.buckets if b.backend == backend and b.scenario == scenario]


def attempt_warm_claim(
    *,
    user: User,
    scenario: str,
    package_digest: str,
    lock_digest: str,
    backend: str,
    instantiation_purpose: InstantiationPurpose,
    range_source: RangeSource,
    workspace_id: int,
    egress_mode: str,
    request_id: UUID,
    override: WarmPoolOverride | None = None,
) -> UUID | None:
    """Attempt to claim a compatible ready warm generation for this launch.

    Returns the claimed generation's Engine ``request_id`` on a hit (the launch
    binds its result to that realized generation), or ``None`` to cold-fall-back.
    Never raises for a miss; a miss is a normal branch.
    """
    from django.conf import settings

    from cms.services import AuditEvent, audit_log
    from cms.services._range_reassign import reassign_range_owner
    from engine.services import claim_ready_generation, enqueue_range_activation
    from shared.audit.vocabulary import AuditAction, AuditActorType, AuditEntityType

    policy: WarmPoolRuntimePolicy = resolve_effective_policy(settings.WARM_POOL_POLICY, override)
    if not policy.is_active():
        return None
    if not backend_supports_warm_activation(backend):
        # A bucket may target a backend without a warm-activation adapter; such a
        # generation can never be safely handed over, so never claim -- cold path.
        return None
    eligible = _eligible_buckets(policy, backend=backend, scenario=scenario)
    if not eligible:
        return None

    isolation = warm_isolation_class(user, workspace_id)
    # The digest is the launch's independently-resolved realization identity (same
    # for every eligible bucket of this scenario/backend). Authorization to a
    # specific bucket is the *separate* eligibility filter: candidates pair each
    # effective-policy bucket id with that digest, so the claim can only take a
    # generation whose realization matches AND whose bucket the current policy still
    # authorizes for this launch.
    digest = compatibility_digest(
        build_compatibility_key(
            backend=backend,
            instantiation_purpose=instantiation_purpose.value,
            range_source=range_source.value,
            workspace_isolation_class=isolation,
            egress_mode=egress_mode,
            scenario=scenario,
            package_digest=package_digest,
            lock_digest=lock_digest,
        )
    )
    candidates = [(bucket.id, digest) for bucket in eligible]

    # Claim + ownership transfer happen in one transaction so a transfer failure
    # rolls the claim back (the generation returns to READY for another launch).
    # Activation is enqueued only after this commits.
    from shared.warm_pool.metrics import CLAIM_FALLBACK, CLAIM_HIT, emit_claim_outcome

    try:
        with transaction.atomic():
            generation = claim_ready_generation(
                candidates=candidates,
                backend=backend,
                range_source=range_source.value,
                claimant_request_id=request_id,
            )
            if generation is None:
                # Warm is enabled for this class but no compatible ready generation
                # in an authorized bucket was available: a genuine miss -> cold path.
                emit_claim_outcome(bucket_id="", backend=backend, outcome=CLAIM_FALLBACK)
                return None
            claimed_request_id = generation.request_id
            generation_uuid = generation.uuid
            claimed_bucket_id = generation.bucket_id
            range_instance = _system_range_instance_for(claimed_request_id)
            if range_instance is None:
                # A claimed generation with no CMS range instance is an inconsistent
                # ledger row; abort the claim (rolls back) and cold-fall-back rather
                # than hand over a half-modeled range.
                logger.error("warm claim: generation %s has no CMS range instance; rolling back", generation_uuid)
                raise _WarmClaimRollback
            reassign_range_owner(range_instance.pk, user, rehome=True)
            audit_log(
                AuditEvent(
                    entity_type=AuditEntityType.RANGE.value,
                    entity_id=range_instance.pk,
                    action=AuditAction.WARM_CLAIM.value,
                    actor_type=AuditActorType.USER.value,
                    actor_id=user.id,
                    new_state={
                        "bucket": claimed_bucket_id,
                        "generation": str(generation_uuid),
                        "claimed_request_id": str(claimed_request_id),
                        "range_source": range_source.value,
                    },
                    request_id=str(claimed_request_id),
                )
            )
    except _WarmClaimRollback:
        return None

    enqueue_range_activation(claimed_request_id)
    emit_claim_outcome(bucket_id=claimed_bucket_id, backend=backend, outcome=CLAIM_HIT)
    logger.info("warm claim: hit bucket=%s generation=%s user_id=%s", claimed_bucket_id, generation_uuid, user.id)
    return claimed_request_id


class _WarmClaimRollback(Exception):
    """Internal sentinel to roll back an inconsistent claim to cold fallback."""


def _system_range_instance_for(request_id: UUID):
    """Return the system-owned CMS RangeInstance correlated to ``request_id``."""
    from cms.models import RangeInstance

    return RangeInstance.objects.filter(request__request_id=request_id).select_related("request").first()
