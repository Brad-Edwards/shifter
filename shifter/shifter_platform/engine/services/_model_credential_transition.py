"""One-use guest refresh across an explicitly admitted model-policy successor."""

from __future__ import annotations

import secrets

from shared.model_access import ContractError
from shared.model_access.network import peer_matches_binding


def refresh_successor(token, peer, moment):
    """Never revive a revoked grant; consume its proof only for a linked successor."""
    from engine.models import ModelAccessCredential, ModelPendingGrant, Range
    from engine.services._model_credentials import _hash, _lock_binding, _parse_token, _rotate, _trusted_subnets

    public_id = _parse_token(token)
    initial = ModelAccessCredential.objects.select_related("grant__allocation").filter(grant_id=public_id).first()
    if initial is None or initial.replacement_grant_id is None:
        return None
    prior = initial.grant.allocation
    range_obj = Range.objects.select_for_update().get(uuid=prior.range_id)
    old = ModelAccessCredential.objects.select_for_update().select_related("grant__allocation").get(pk=initial.pk)
    if (
        old.hard_expires_at <= moment
        or not old.refresh_hash
        or not secrets.compare_digest(old.refresh_hash, _hash(token))
        or old.grant.state != "revoked"
    ):
        raise ContractError("credential.unavailable")
    # Only a server-written successor link crosses the revocation boundary.
    # Several admin edits before a guest refresh can form a bounded chain.
    successor_id = old.replacement_grant_id
    visited = set()
    for _ in range(32):
        if successor_id is None or successor_id in visited:
            raise ContractError("credential.unavailable")
        visited.add(successor_id)
        grant = ModelPendingGrant.objects.select_related("allocation").get(pk=successor_id)
        candidate = grant.allocation
        if (
            candidate.range_id != prior.range_id
            or candidate.operation_id != prior.operation_id
            or candidate.workload_role != prior.workload_role
            or candidate.source_policy_revision <= prior.source_policy_revision
            or candidate.snapshot["request"]["owner_ref"] != prior.snapshot["request"]["owner_ref"]
        ):
            raise ContractError("credential.unavailable")
        if grant.state != "revoked":
            break
        successor_id = (
            ModelAccessCredential.objects.filter(grant=grant).values_list("replacement_grant_id", flat=True).first()
        )
    else:
        raise ContractError("credential.unavailable")
    allocation, grant, current_range = _lock_binding(candidate.pk, moment)
    if (
        allocation.source_policy_revision != range_obj.model_source_policy_revision
        or grant.state != "pending"
        or ModelAccessCredential.objects.filter(grant=grant).exists()
    ):
        raise ContractError("credential.unavailable")
    subnets = _trusted_subnets(current_range, allocation)
    if subnets != old.admitted_subnets or not any(peer_matches_binding(peer, subnet) for subnet in subnets):
        raise ContractError("credential.unavailable")
    credential = ModelAccessCredential.objects.create(
        grant=grant,
        grant_epoch=grant.grant_epoch,
        admitted_subnets=subnets,
        enrollment_expires_at=moment,
        hard_expires_at=min(old.hard_expires_at, allocation.deadline),
    )
    old.refresh_hash = old.access_hash = old.enrollment_hash = ""
    old.save(update_fields=["refresh_hash", "access_hash", "enrollment_hash", "updated_at"])
    grant.state = "active"
    grant.save(update_fields=["state"])
    return _rotate(grant, credential, moment)
