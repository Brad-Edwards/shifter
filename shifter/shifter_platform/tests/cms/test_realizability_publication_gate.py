"""Publication gate: a non-realizable ACES pack cannot be enabled (#1581, ADR-034-R3).

ADR-034-R3 forbids loopholes. The editor badge is advisory -- an author could
ignore it, script the API directly, or race a registry change -- so the
authoritative check runs at the mutation boundary that flips
``ScenarioMetadata.enabled`` to true, recomputing realizability rather than
trusting anything the client sends.

These tests drive the enforcement through its real boundary
(``cms.scenario_editor.services.update_metadata``) and assert the effect: the
write does not happen. Remove the gate and they go red.

Saving a non-realizable pack for staff review stays allowed, and so does
disabling one -- the gate blocks publication only.
"""

from __future__ import annotations

import pytest
from django.conf import settings

from cms.models import ScenarioMetadata
from cms.scenario_editor._common import ScenarioEditorError
from cms.scenario_editor.services import update_metadata
from cms.scenarios.pack_validation import pack_digest
from cms.services import PackRegistrationRequest, register_pack
from engine.services import AcesImageMappingOptions, upsert_aces_image_mapping
from tests.cms.conftest import IMAGELESS_PACK_SDL

pytestmark = pytest.mark.django_db

_GCE = "gce"


@pytest.fixture
def staff_user(django_user_model):
    return django_user_model.objects.create_user(
        username="gate-staff@example.com", email="gate-staff@example.com", is_staff=True
    )


@pytest.fixture(autouse=True)
def _gcp_target(monkeypatch):
    monkeypatch.setattr(settings, "CLOUD_PROVIDER", "gcp")


@pytest.fixture
def aces_pack(staff_user, make_pack, tmp_path, monkeypatch):
    root = make_pack(tmp_path / "packs" / "imageless", name="imageless", sdl=IMAGELESS_PACK_SDL)
    monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", str(tmp_path))
    register_pack(
        user=staff_user,
        request=PackRegistrationRequest(
            scenario_id="imageless",
            source_kind="repo",
            contract_kind="aces",
            contract_profile="shifter",
            package_ref="packs/imageless",
            package_version="0.1.0",
            package_digest=pack_digest(root),
            provenance={"repo": "acme/example", "commit": "c" * 40},
        ),
    )
    return root


def _supply_base_image() -> None:
    upsert_aces_image_mapping(
        provider=_GCE,
        source_name="linux",
        image_ref="projects/p/global/images/base-linux",
        options=AcesImageMappingOptions(source_version=""),
    )


class TestPublicationBlocked:
    """enabled=true is refused while the backend cannot realize the pack."""

    def test_enabling_a_non_realizable_pack_is_refused(self, staff_user, aces_pack):
        # No image mapping registered: the pack has a proven supply gap.
        with pytest.raises(ScenarioEditorError):
            update_metadata(staff_user, "imageless", enabled=True)

    def test_refused_publication_does_not_persist_enabled(self, staff_user, aces_pack):
        with pytest.raises(ScenarioEditorError):
            update_metadata(staff_user, "imageless", enabled=True)

        metadata = ScenarioMetadata.objects.filter(scenario_id="imageless").first()
        assert metadata is None or metadata.enabled is False

    def test_gap_reason_reaches_the_author(self, staff_user, aces_pack):
        with pytest.raises(ScenarioEditorError) as excinfo:
            update_metadata(staff_user, "imageless", enabled=True)

        assert "realiz" in str(excinfo.value).lower()

    def test_absent_assessment_is_refused(self, staff_user, aces_pack, monkeypatch):
        # Existence verification and assessment are separate lookups, so a
        # catalog entry that disappears or resolves inconsistently between them
        # must fail closed. "No assessment" is not "nothing to assess".
        monkeypatch.setattr("cms.scenario_editor._metadata.get_scenario_realizability", lambda _id: None)

        with pytest.raises(ScenarioEditorError):
            update_metadata(staff_user, "imageless", enabled=True)

    def test_absent_assessment_does_not_persist_enabled(self, staff_user, aces_pack, monkeypatch):
        monkeypatch.setattr("cms.scenario_editor._metadata.get_scenario_realizability", lambda _id: None)

        with pytest.raises(ScenarioEditorError):
            update_metadata(staff_user, "imageless", enabled=True)

        metadata = ScenarioMetadata.objects.filter(scenario_id="imageless").first()
        assert metadata is None or metadata.enabled is False

    def test_indeterminate_is_also_refused(self, staff_user, aces_pack, monkeypatch):
        # Cannot assess is not permission to publish.
        _supply_base_image()
        monkeypatch.setattr(settings, "ACES_PACKAGE_ROOT", "/nonexistent-root")

        with pytest.raises(ScenarioEditorError):
            update_metadata(staff_user, "imageless", enabled=True)


class TestPublicationAllowed:
    """The gate blocks publication only -- not saving, disabling, or legacy work."""

    def test_realizable_pack_can_be_enabled(self, staff_user, aces_pack):
        _supply_base_image()

        metadata = update_metadata(staff_user, "imageless", enabled=True)

        assert metadata.enabled is True

    def test_disabling_a_non_realizable_pack_is_allowed(self, staff_user, aces_pack):
        # Staff must always be able to withdraw a pack, gap or no gap.
        metadata = update_metadata(staff_user, "imageless", enabled=False)
        assert metadata.enabled is False

    def test_staff_only_toggle_without_enabling_is_allowed(self, staff_user, aces_pack):
        metadata = update_metadata(staff_user, "imageless", staff_only=True)
        assert metadata.staff_only is True

    def test_legacy_scenario_is_unaffected_by_the_gate(self, staff_user, hydratable_scenario):
        # Legacy entries are not_applicable; the ACES gate must not block them.
        metadata = update_metadata(staff_user, hydratable_scenario.scenario_id, enabled=True)
        assert metadata.enabled is True
