"""Uniform, entitlement-blind pack registration service (#1578, ADR-034).

Pins the single CMS registration boundary: it is source-agnostic and
entitlement-blind, authorizes WHO may register (never whether they were entitled
to obtain the pack), validates the incoming pack as foreign input, binds the
catalog id to the pack's validated identity, never lets a caller assert
conformance, fails closed on legacy-id shadowing and duplicates, keeps
object-backed packs non-launchable until #1567, and audits every registration.
"""

from __future__ import annotations

import dataclasses

import pytest
from django.contrib.auth import get_user_model

from cms.exceptions import CMSError
from cms.models import AcesPackageSource, Scenario
from cms.scenarios.registry import get_catalog_entry
from cms.services import PackRegistrationRequest, register_pack
from risk_register.models import AuditLog

User = get_user_model()

pytestmark = pytest.mark.django_db

# The conformant repo pack fixtures are built with this name; a repo pack's
# catalog id must equal its validated pack identity (finding: scenario_id binding).
FIXTURE_PACK_NAME = "ingestion-fixture"


@pytest.fixture
def staff_user(db):
    return User.objects.create_user(
        username="ingestion-staff@example.com",
        email="ingestion-staff@example.com",
        is_staff=True,
    )


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(
        username="ingestion-regular@example.com",
        email="ingestion-regular@example.com",
        is_staff=False,
    )


@pytest.fixture
def repo_pack(make_pack, tmp_path, monkeypatch):
    """A conformant repo-backed pack under a monkeypatched ACES_PACKAGE_ROOT.

    Returns the pack's package_ref (relative to the configured package root). Its
    validated identity is ``FIXTURE_PACK_NAME``.
    """
    from django.conf import settings

    make_pack(tmp_path / "packs" / "fixture", name=FIXTURE_PACK_NAME)
    monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(tmp_path))
    return "packs/fixture"


def _request(package_ref: str, **overrides) -> PackRegistrationRequest:
    fields = {
        "scenario_id": FIXTURE_PACK_NAME,
        "source_kind": "repo",
        "contract_kind": "aces",
        "contract_profile": "shifter",
        "package_ref": package_ref,
        "package_version": "0.1.0",
        "package_digest": "sha256:" + "a" * 64,
        "provenance": {"repo": "acme/example", "commit": "c" * 40},
    }
    fields.update(overrides)
    return PackRegistrationRequest(**fields)


class TestRegisterPackHappyPath:
    def test_persists_row_and_appears_in_catalog(self, staff_user, repo_pack):
        result = register_pack(user=staff_user, request=_request(repo_pack))
        assert result.scenario_id == FIXTURE_PACK_NAME
        assert result.created is True
        row = AcesPackageSource.objects.get(scenario_id=FIXTURE_PACK_NAME)
        assert row.registered_by_id == staff_user.id
        assert get_catalog_entry(FIXTURE_PACK_NAME) is not None

    def test_records_audit_event(self, staff_user, repo_pack):
        register_pack(user=staff_user, request=_request(repo_pack))
        entries = AuditLog.objects.filter(entity_type=AuditLog.EntityType.SCENARIO, action=AuditLog.Action.CREATE)
        assert any(FIXTURE_PACK_NAME in str(e.new_state) for e in entries)


class TestRegisterPackAuthorization:
    def test_non_staff_is_denied(self, regular_user, repo_pack):
        from django.core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            register_pack(user=regular_user, request=_request(repo_pack))
        assert not AcesPackageSource.objects.filter(scenario_id=FIXTURE_PACK_NAME).exists()

    def test_none_user_is_rejected(self, repo_pack):
        with pytest.raises(TypeError):
            register_pack(user=None, request=_request(repo_pack))


class TestRegisterPackEntitlementBlind:
    def test_provenance_does_not_gate_acceptance(self, staff_user, make_pack, tmp_path, monkeypatch):
        # Two packs whose registrations differ only in provenance (a public vs a
        # private-looking origin) both succeed: nothing branches on how a pack was
        # obtained. Distinct packs (with matching identities) are used because a
        # repo pack's catalog id is bound to its validated identity.
        from django.conf import settings

        make_pack(tmp_path / "packs" / "pub", name="pack-public")
        make_pack(tmp_path / "packs" / "priv", name="pack-private")
        monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(tmp_path))

        public = register_pack(
            user=staff_user,
            request=_request("packs/pub", scenario_id="pack-public", provenance={"repo": "public/catalog"}),
        )
        private = register_pack(
            user=staff_user,
            request=_request("packs/priv", scenario_id="pack-private", provenance={"repo": "licensed/private"}),
        )
        assert public.created and private.created
        assert AcesPackageSource.objects.filter(scenario_id__in=["pack-public", "pack-private"]).count() == 2


class TestRegisterPackConformanceIsNotCallerAsserted:
    def test_registration_always_lands_non_passed(self, staff_user, repo_pack):
        # A caller cannot promote its own pack to conformance-passed; conformance
        # is established out of band by a trusted process.
        register_pack(user=staff_user, request=_request(repo_pack))
        row = AcesPackageSource.objects.get(scenario_id=FIXTURE_PACK_NAME)
        assert row.conformance_status == AcesPackageSource.ConformanceStatus.PENDING
        assert row.conformance_report_ref == ""

    def test_request_has_no_conformance_fields(self):
        field_names = {f.name for f in dataclasses.fields(PackRegistrationRequest)}
        assert "conformance_status" not in field_names
        assert "conformance_report_ref" not in field_names


class TestRegisterPackIdentityBinding:
    def test_rejects_scenario_id_not_matching_pack_identity(self, staff_user, repo_pack):
        # The pack's validated identity is FIXTURE_PACK_NAME; a different catalog
        # id would let one immutable pack be aliased under many ids. Asserting the
        # bounded message pins the identity guard specifically (a different guard
        # would carry a different message and fail this match).
        with pytest.raises(CMSError, match="validated identity"):
            register_pack(user=staff_user, request=_request(repo_pack, scenario_id="some-other-id"))
        assert not AcesPackageSource.objects.filter(scenario_id="some-other-id").exists()


class TestRegisterPackFailsClosed:
    """Each guard is isolated so deleting it fails a test, not masked by an
    adjacent guard that raises the same CMSError for the same crafted input.
    """

    def test_rejects_shadow_of_yaml_default(self, staff_user, make_pack, tmp_path, monkeypatch):
        from django.conf import settings

        # The pack's validated identity equals the shadowed id, so the identity
        # guard would NOT fire: deleting the shadow branch would let registration
        # succeed, failing this test.
        make_pack(tmp_path / "packs" / "basic", name="basic")
        monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(tmp_path))
        with pytest.raises(CMSError, match="shadow"):
            register_pack(user=staff_user, request=_request("packs/basic", scenario_id="basic"))
        assert not AcesPackageSource.objects.filter(scenario_id="basic").exists()

    def test_rejects_shadow_of_active_db_custom(self, staff_user, make_pack, tmp_path, monkeypatch, valid_db_scenario):
        from django.conf import settings

        make_pack(tmp_path / "packs" / "dbshadow", name=valid_db_scenario)
        monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(tmp_path))
        with pytest.raises(CMSError, match="shadow"):
            register_pack(user=staff_user, request=_request("packs/dbshadow", scenario_id=valid_db_scenario))

    def test_rejects_duplicate_registration(self, staff_user, repo_pack):
        register_pack(user=staff_user, request=_request(repo_pack))
        with pytest.raises(CMSError, match="already registered"):
            register_pack(user=staff_user, request=_request(repo_pack))
        assert AcesPackageSource.objects.filter(scenario_id=FIXTURE_PACK_NAME).count() == 1

    def test_rejects_malformed_pack(self, staff_user, make_pack, tmp_path, monkeypatch):
        from django.conf import settings

        make_pack(tmp_path / "packs" / "broken", name="broken-pack", sdl=None)
        monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(tmp_path))
        with pytest.raises(CMSError, match="ingestion validation"):
            register_pack(user=staff_user, request=_request("packs/broken", scenario_id="broken-pack"))

    def test_rejects_missing_repo_pack(self, staff_user, tmp_path, monkeypatch):
        from django.conf import settings

        monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(tmp_path))
        with pytest.raises(CMSError, match="ingestion validation"):
            register_pack(user=staff_user, request=_request("packs/does-not-exist"))

    def test_rejects_package_ref_escaping_root(self, staff_user, make_pack, tmp_path, monkeypatch):
        from django.conf import settings

        # A real, conformant pack lives OUTSIDE the configured root. Only the
        # containment check rejects it: delete that check and the ref resolves to
        # the real pack, validates, and its identity matches, so registration
        # would succeed and fail this test.
        root = tmp_path / "root"
        root.mkdir()
        make_pack(tmp_path / "outside" / "escape-target", name="escape-target")
        monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(root))
        with pytest.raises(CMSError, match="escapes the configured package root"):
            register_pack(
                user=staff_user,
                request=_request("../outside/escape-target", scenario_id="escape-target"),
            )
        assert not AcesPackageSource.objects.filter(scenario_id="escape-target").exists()


class TestRegisterPackObjectSource:
    def test_object_registers_pending_and_not_launchable(self, staff_user):
        # Object-backed packs skip content resolution (no local resolver until
        # #1567); they register non-launchable and are never conformance-passed.
        result = register_pack(
            user=staff_user,
            request=_request("object-key/pack", scenario_id="obj-pending", source_kind="object"),
        )
        assert result.created is True
        row = AcesPackageSource.objects.get(scenario_id="obj-pending")
        assert row.conformance_status == AcesPackageSource.ConformanceStatus.PENDING
        assert get_catalog_entry("obj-pending")["launchable"] is False


@pytest.fixture
def valid_db_scenario(staff_user):
    Scenario.objects.create(
        scenario_id="db-custom-shadow",
        name="DB Custom Shadow",
        description="Active DB custom used to test no-shadow.",
        definition={
            "instances": [{"name": "A", "role": "attacker", "os_type": "kali", "xdr_agent": False}],
            "subnets": [{"name": "n", "instances": ["A"]}],
            "ngfw": False,
        },
        created_by=staff_user,
        updated_by=staff_user,
    )
    return "db-custom-shadow"


def test_request_is_immutable():
    req = _request("packs/fixture")
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.scenario_id = "mutated"  # type: ignore[misc]
