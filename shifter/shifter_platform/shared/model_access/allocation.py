"""Versioned deterministic shard-ranking algorithms from ADR-060."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from struct import pack
from uuid import UUID

from shared.model_access.models import AllocationStrategy, Identifier

_ASCII_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$", re.ASCII)
_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$", re.ASCII)


class AllocationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ShardWeight:
    shard_id: Identifier
    weight: int

    def __post_init__(self) -> None:
        if not isinstance(self.shard_id, str) or not _ASCII_ID.fullmatch(self.shard_id):
            raise ValueError("shard_id must be bounded ASCII")
        if isinstance(self.weight, bool) or not isinstance(self.weight, int) or not 1 <= self.weight <= 64:
            raise ValueError("weight must be an integer from 1 through 64")


@dataclass(frozen=True)
class RankedShard:
    shard_id: str
    score: int

    @property
    def score_hex(self) -> str:
        return self.score.to_bytes(32, "big").hex()


def _field(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return pack(">I", len(encoded)) + encoded


def _slot_score(parts: tuple[str, ...], slot: int) -> int:
    digest = sha256(b"".join(_field(part) for part in parts) + pack(">I", slot)).digest()
    return int.from_bytes(digest, "big")


def rank_weighted_rendezvous(
    *,
    deployment_id: UUID,
    allocation_group_id: UUID,
    policy_digest: str,
    logical_alias: str,
    shards: tuple[ShardWeight, ...],
) -> tuple[RankedShard, ...]:
    if not isinstance(deployment_id, UUID) or not isinstance(allocation_group_id, UUID):
        raise AllocationError("allocation.invalid_uuid")
    if not shards:
        raise AllocationError("allocation.empty_shards")
    if len(shards) > 32:
        raise AllocationError("allocation.too_many_shards")
    if len({item.shard_id for item in shards}) != len(shards):
        raise AllocationError("allocation.duplicate_shard")
    match = _DIGEST.fullmatch(policy_digest)
    if match is None:
        raise AllocationError("allocation.invalid_policy_digest")
    if not _ASCII_ID.fullmatch(logical_alias):
        raise AllocationError("allocation.invalid_alias")
    common = (
        "shifter/model-access/v1",
        str(deployment_id).lower(),
        str(allocation_group_id).lower(),
        match.group(1),
        logical_alias,
    )
    ranked = tuple(
        RankedShard(
            shard_id=shard.shard_id,
            score=max(_slot_score((*common, shard.shard_id), slot) for slot in range(shard.weight)),
        )
        for shard in shards
    )
    return tuple(sorted(ranked, key=lambda item: (-item.score, item.shard_id.encode("ascii"))))


def select_shard(
    *,
    strategy: AllocationStrategy,
    deployment_id: UUID,
    allocation_group_id: UUID,
    policy_digest: str,
    logical_alias: str,
    shards: tuple[ShardWeight, ...],
) -> str:
    if strategy is AllocationStrategy.FIXED_V1:
        if len(shards) != 1:
            raise AllocationError("allocation.fixed_cardinality")
        return shards[0].shard_id
    if strategy is AllocationStrategy.WEIGHTED_RENDEZVOUS_V1:
        return rank_weighted_rendezvous(
            deployment_id=deployment_id,
            allocation_group_id=allocation_group_id,
            policy_digest=policy_digest,
            logical_alias=logical_alias,
            shards=shards,
        )[0].shard_id
    raise AllocationError("allocation.unsupported_strategy")
