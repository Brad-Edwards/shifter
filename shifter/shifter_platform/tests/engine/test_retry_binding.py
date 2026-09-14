"""Behavioral tests for the public-operation retry binding (#2086, ADR-063-R1/R2).

First use mints and binds; a same-intent replay recovers the original operation
without re-minting; a different-intent replay conflicts before any effect. The
real concurrency proofs live in ``test_retry_binding_postgres.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from engine.models import PublicOperationRetryBinding, RetryBindingStatus
from engine.retry_binding import (
    MintedOperation,
    RetryKeyConflict,
    bind_public_operation,
    lookup_public_operation,
)

pytestmark = pytest.mark.django_db

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
KEY = {
    "deployment_scope": "proj-x",
    "actor_key": "7",
    "action": "raes-range:provision",
    "caller_key": "k-1",
}


def _mint() -> MintedOperation:
    return MintedOperation(request_id=str(uuid4()), operation_id=str(uuid4()))


def test_first_use_mints_and_binds():
    calls: list[int] = []

    def mint() -> MintedOperation:
        calls.append(1)
        return _mint()

    result = bind_public_operation(**KEY, intent_digest=DIGEST_A, intent_projection_version="1", mint=mint)

    assert result.created is True
    assert len(calls) == 1
    assert result.binding.intent_digest == DIGEST_A
    assert result.binding.status == RetryBindingStatus.ACTIVE
    assert PublicOperationRetryBinding.objects.filter(**KEY).count() == 1


def test_replay_same_intent_recovers_without_reminting():
    first = bind_public_operation(**KEY, intent_digest=DIGEST_A, intent_projection_version="1", mint=_mint)
    calls: list[int] = []

    def mint() -> MintedOperation:
        calls.append(1)
        return _mint()

    second = bind_public_operation(**KEY, intent_digest=DIGEST_A, intent_projection_version="1", mint=mint)

    assert second.created is False
    assert calls == []
    assert str(second.binding.operation_id) == str(first.binding.operation_id)
    assert PublicOperationRetryBinding.objects.filter(**KEY).count() == 1


def test_replay_different_intent_conflicts_without_reminting():
    bind_public_operation(**KEY, intent_digest=DIGEST_A, intent_projection_version="1", mint=_mint)
    calls: list[int] = []

    def mint() -> MintedOperation:
        calls.append(1)
        return _mint()

    with pytest.raises(RetryKeyConflict):
        bind_public_operation(**KEY, intent_digest=DIGEST_B, intent_projection_version="1", mint=mint)

    assert calls == []
    assert PublicOperationRetryBinding.objects.filter(**KEY).count() == 1


def test_different_caller_key_is_a_separate_binding():
    bind_public_operation(**KEY, intent_digest=DIGEST_A, intent_projection_version="1", mint=_mint)
    other = {**KEY, "caller_key": "k-2"}

    result = bind_public_operation(**other, intent_digest=DIGEST_A, intent_projection_version="1", mint=_mint)

    assert result.created is True
    assert PublicOperationRetryBinding.objects.count() == 2


def test_lookup_returns_none_when_absent():
    assert lookup_public_operation(**KEY) is None
