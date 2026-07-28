"""Workspace scope binding on the RAES-native launch path (ADR-046-R3, #1325).

The cyberscript launch path is covered by ``test_range_workspace_binding``. The
RAES path is a second, independent way a range comes into existence, so it gets
its own coverage: a range created here must be scoped exactly like one created
through the cyberscript path, or RAES launches would quietly produce unbound
ranges.
"""

from __future__ import annotations

import pytest

from cms.models import RaesPackageSource, RangeInstance
from cms.scenarios.pack_validation import pack_digest
from cms.services import create_raes_native_range
from workspaces import services as workspace_services

_PACK_REF = "packs/raes-ws"


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create(username="raes-ws-launcher")


@pytest.fixture
def native_on(monkeypatch):
    from django.conf import settings

    monkeypatch.setattr(settings, "RAES_NATIVE_PROVISIONING_ENABLED", True)


def _make_source(user, digest, scenario_id="raes-ws"):
    return RaesPackageSource.objects.create(
        scenario_id=scenario_id,
        contract_kind="raes",
        contract_profile="shifter",
        source_kind="repo",
        package_ref=_PACK_REF,
        package_version="1.0.0",
        package_digest=digest,
        conformance_status="passed",
        registered_by=user,
    )


@pytest.mark.django_db
def test_raes_launch_binds_cms_rows_and_carries_the_scope_to_engine(user, native_on, make_pack, tmp_path, monkeypatch):
    """The scope reaches the engine seam as a trusted argument, like backend_admission."""
    from django.conf import settings

    from engine.services import RaesRangeRef

    root = make_pack(tmp_path / _PACK_REF, name="raes-ws")
    monkeypatch.setattr(settings, "RAES_PACKAGE_ROOT", str(tmp_path))
    captured = {}

    def fake_create_raes_range(
        *, request_id, user_id, compiled_plan, backend_admission=None, delivery_bindings=(), workspace_id=None
    ):
        captured["workspace_id"] = workspace_id
        return RaesRangeRef(request_id=request_id, accepted=True, status="accepted", range_id="rng-1")

    monkeypatch.setattr("cms.raes.dispatch.create_raes_range", fake_create_raes_range)
    _make_source(user, pack_digest(root))

    ctx = create_raes_native_range(user, "raes-ws")

    expected = workspace_services.resolve_personal_workspace(user).workspace_id
    instance = RangeInstance.objects.get(request__request_id=ctx.request_id)
    assert instance.workspace_id == expected
    assert instance.request.workspace_id == expected
    assert captured["workspace_id"] == expected
