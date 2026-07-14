"""Tests for the committed ``/api/v1/`` OpenAPI contract and its gates (#1329).

Integration-style: the published document is generated once per module and many
assertions read it, rather than one mocked micro-test per property. The
breaking-change gate mocks only the true subprocess boundary (oasdiff/git), per
the boundary-mock policy.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from shared.api import contract
from shared.api.schema import exclude_unpublished_endpoints


@pytest.fixture(scope="module")
def openapi_document() -> dict[str, Any]:
    """The canonical published document, generated from the live DRF surface.

    Generation runs with validation and fail-on-warn, so an unresolved
    serializer, operation-id collision, or schema warning fails this fixture and
    therefore the whole module.
    """
    return json.loads(contract.generate_openapi_document())


class _Callback:
    """Stand-in for a resolved DRF view callback carrying its view class."""

    def __init__(self, module: str) -> None:
        self.cls = type("_View", (), {"__module__": module})


class TestExclusionHook:
    def test_drops_unpublished_app_keeps_published(self) -> None:
        ctf = ("/api/v1/ctf/events/", "^ctf$", "GET", _Callback("ctf.api._base"))
        mission_control = (
            "/api/v1/mission-control/range/",
            "^mc$",
            "GET",
            _Callback("mission_control.api.ranges"),
        )
        result = exclude_unpublished_endpoints([ctf, mission_control])
        assert ctf not in result
        assert mission_control in result


class TestPublishedContract:
    def test_ctf_surface_excluded(self, openapi_document: dict[str, Any]) -> None:
        assert not any(path.startswith("/api/v1/ctf/") for path in openapi_document["paths"])

    def test_mission_control_post_has_request_body(self, openapi_document: dict[str, Any]) -> None:
        launch = openapi_document["paths"]["/api/v1/mission-control/range/launch/"]["post"]
        assert "requestBody" in launch

    def test_error_envelope_is_a_reusable_component(self, openapi_document: dict[str, Any]) -> None:
        schemas = openapi_document["components"]["schemas"]
        assert "ApiError" in schemas
        assert "ApiErrorBody" in schemas
        body = schemas["ApiErrorBody"]["properties"]
        assert {"code", "message"} <= set(body)

    def test_error_responses_reference_the_envelope(self, openapi_document: dict[str, Any]) -> None:
        # Only the statuses the shared exception handler guarantees are injected.
        risks = openapi_document["paths"]["/api/v1/risks/"]["get"]
        for code in ("401", "403"):
            schema = risks["responses"][code]["content"]["application/json"]["schema"]
            assert schema["$ref"].endswith("/ApiError")

    def test_body_dependent_errors_are_not_injected_globally(self, openapi_document: dict[str, Any]) -> None:
        # 400/404 shapes vary per endpoint (some legacy views return non-envelope
        # errors), so they must not be blanket-injected onto every operation.
        risks = openapi_document["paths"]["/api/v1/risks/"]["get"]
        assert "400" not in risks["responses"]
        assert "404" not in risks["responses"]

    def test_created_endpoints_declare_201(self, openapi_document: dict[str, Any]) -> None:
        # NGFW/credential creates return 201; the contract must not claim 200.
        ngfw = openapi_document["paths"]["/api/v1/mission-control/ngfw/"]["post"]
        assert "201" in ngfw["responses"]
        assert "200" not in ngfw["responses"]

    def test_token_scopes_published_for_scoped_operations(self, openapi_document: dict[str, Any]) -> None:
        assert openapi_document["paths"]["/api/v1/risks/"]["get"]["x-required-scopes"] == ["risk:read"]

    def test_unscoped_operations_omit_scope_extension(self, openapi_document: dict[str, Any]) -> None:
        # Admin-only audit reads are not token-scoped; they must not advertise a scope.
        assert "x-required-scopes" not in openapi_document["paths"]["/api/v1/audit/"]["get"]

    def test_method_scoped_permissions_are_reported(self, openapi_document: dict[str, Any]) -> None:
        # ScenarioResourceView resolves scopes per method via get_permissions().
        detail = openapi_document["paths"]["/api/v1/cms/scenario-editor/scenarios/{scenario_id}/"]
        assert detail["get"]["x-required-scopes"] == ["cms:authoring:read"]
        assert detail["patch"]["x-required-scopes"] == ["cms:authoring:write"]

    def test_comment_author_resolves_to_structured_component(self, openapi_document: dict[str, Any]) -> None:
        schemas = openapi_document["components"]["schemas"]
        assert "CommentAuthor" in schemas
        author = schemas["Comment"]["properties"]["author"]
        assert any("CommentAuthor" in ref.get("$ref", "") for ref in author.get("allOf", []))

    def test_both_auth_schemes_present(self, openapi_document: dict[str, Any]) -> None:
        assert {"ApiTokenAuth", "cookieAuth"} <= set(openapi_document["components"]["securitySchemes"])


@pytest.mark.django_db
class TestLiveResponseParity:
    """Request-level parity: a live response must agree with the published schema."""

    def test_unauthenticated_request_matches_published_401(self, openapi_document: dict[str, Any]) -> None:
        from rest_framework.test import APIClient

        response = APIClient().get("/api/v1/risks/")
        assert response.status_code == 401
        # Live body is the canonical envelope the exception handler renders...
        body = response.json()
        assert {"code", "message"} <= set(body["error"])
        # ...and the contract publishes exactly that shape for 401 on this operation.
        published = openapi_document["paths"]["/api/v1/risks/"]["get"]["responses"]["401"]
        assert published["content"]["application/json"]["schema"]["$ref"].endswith("/ApiError")


class TestDriftGate:
    def test_committed_artifact_matches_the_drf_surface(self) -> None:
        is_current, detail = contract.check_drift()
        assert is_current, detail


class TestBreakingChangeGate:
    def test_breaking_change_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            contract.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="1 error: api path removed", stderr=""),
        )
        is_compatible, detail = contract.check_breaking_changes("{}", "{}", oasdiff_bin="oasdiff")
        assert not is_compatible
        assert "error" in detail

    def test_compatible_change_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            contract.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="No breaking changes", stderr=""),
        )
        is_compatible, _detail = contract.check_breaking_changes("{}", "{}", oasdiff_bin="oasdiff")
        assert is_compatible

    def test_new_major_skips_when_base_artifact_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract, "resolve_base_document", lambda base_ref, major=contract.API_MAJOR: None)
        ok, detail = contract.check_breaking_against("origin/dev")
        assert ok
        assert "skipped" in detail.lower()

    @staticmethod
    def _fake_git(*, verify_rc: int, ls_tree_rc: int, ls_tree_out: str, show_out: str = "{}"):
        def fake_git(*args: str) -> SimpleNamespace:
            if args[:2] == ("rev-parse", "--show-toplevel"):
                return SimpleNamespace(returncode=0, stdout="/\n", stderr="")
            if args[0] == "rev-parse":
                return SimpleNamespace(returncode=verify_rc, stdout="", stderr="bad ref")
            if args[0] == "ls-tree":
                return SimpleNamespace(returncode=ls_tree_rc, stdout=ls_tree_out, stderr="ls-tree failed")
            if args[0] == "show":
                return SimpleNamespace(returncode=0, stdout=show_out, stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        return fake_git

    def test_resolve_base_returns_none_when_path_genuinely_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Ref resolves, ls-tree succeeds with empty output -> genuine first publication.
        monkeypatch.setattr(contract, "_git", self._fake_git(verify_rc=0, ls_tree_rc=0, ls_tree_out=""))
        assert contract.resolve_base_document("origin/dev") is None

    def test_resolve_base_fails_closed_on_unresolvable_ref(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(contract, "_git", self._fake_git(verify_rc=1, ls_tree_rc=0, ls_tree_out=""))
        with pytest.raises(RuntimeError, match="could not be resolved"):
            contract.resolve_base_document("bogus-ref")

    def test_resolve_base_fails_closed_on_lookup_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Ref resolves but the path query itself errors -> must raise, never skip.
        monkeypatch.setattr(contract, "_git", self._fake_git(verify_rc=0, ls_tree_rc=128, ls_tree_out=""))
        with pytest.raises(RuntimeError, match="failed to query"):
            contract.resolve_base_document("origin/dev")

    def test_resolve_base_reads_present_artifact(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            contract,
            "_git",
            self._fake_git(verify_rc=0, ls_tree_rc=0, ls_tree_out="100644 blob abc\topenapi/v1.json\n", show_out="{}"),
        )
        assert contract.resolve_base_document("origin/dev") == "{}"
