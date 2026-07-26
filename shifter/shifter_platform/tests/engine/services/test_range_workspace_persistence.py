"""The Engine create seams persist the exact workspace scope they are given (#1325).

Every other engine suite passes ``workspace_id`` into a create seam because the
argument is required, but passing it proves nothing on its own: if
``_persist_range_atomically`` dropped the field, mis-keyed it, or hard-coded a
constant, those suites would all still pass while the ADR-046-R3 binding
guarantee silently disappeared.

This module is where that guarantee is actually checked -- the value is read back
off the persisted row, two different scopes are proven to diverge (which a
hard-coded constant would fail), and the fail-closed guard is exercised on both
create paths.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from engine.models import Range
from engine.services import create_aces_range, create_range
from engine.services._common import EngineError
from tests.engine.services.test_create_range import make_request_spec

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="ws-persist@e.com", email="ws-persist@e.com")


def _spec(user):
    return make_request_spec(user_id=user.id)


def _aces_plan():
    return {"kind": "aces_provisioning_plan", "aces_sdl_version": "1.0", "resources": []}


# ---------------------------------------------------------------------------
# cyberscript create_range
# ---------------------------------------------------------------------------


def test_create_range_persists_the_exact_workspace_it_was_given(user):
    ref = create_range(_spec(user), workspace_id=4242)

    assert Range.objects.get(request__request_id=ref.request_id).workspace_id == 4242


def test_two_ranges_with_different_scopes_do_not_collapse_to_one(user):
    """A hard-coded or shared binding would make these equal."""
    first = create_range(_spec(user), workspace_id=101)
    second = create_range(_spec(user), workspace_id=202)

    assert Range.objects.get(request__request_id=first.request_id).workspace_id == 101
    assert Range.objects.get(request__request_id=second.request_id).workspace_id == 202


def test_create_range_refuses_a_missing_workspace_and_persists_nothing(user):
    spec = _spec(user)

    with pytest.raises(EngineError):
        create_range(spec, workspace_id=None)

    assert not Range.objects.filter(request__request_id=spec.request_id).exists()


# ---------------------------------------------------------------------------
# ACES create_aces_range
# ---------------------------------------------------------------------------


def test_create_aces_range_persists_the_exact_workspace_it_was_given(user):
    request_id = uuid4()

    create_aces_range(request_id=request_id, user_id=user.id, compiled_plan=_aces_plan(), workspace_id=7373)

    assert Range.objects.get(request__request_id=request_id).workspace_id == 7373


def test_two_aces_ranges_with_different_scopes_do_not_collapse_to_one(user):
    first_id, second_id = uuid4(), uuid4()

    create_aces_range(request_id=first_id, user_id=user.id, compiled_plan=_aces_plan(), workspace_id=11)
    create_aces_range(request_id=second_id, user_id=user.id, compiled_plan=_aces_plan(), workspace_id=22)

    assert Range.objects.get(request__request_id=first_id).workspace_id == 11
    assert Range.objects.get(request__request_id=second_id).workspace_id == 22


def test_create_aces_range_refuses_a_missing_workspace_and_persists_nothing(user):
    request_id = uuid4()

    plan = _aces_plan()

    with pytest.raises(EngineError):
        create_aces_range(request_id=request_id, user_id=user.id, compiled_plan=plan, workspace_id=None)

    assert not Range.objects.filter(request__request_id=request_id).exists()
