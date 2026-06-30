"""DRF boundary coverage for the canonical CMS API (PLAT-106 / #1122)."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Group
from django.test import override_settings
from django.urls import Resolver404, clear_url_caches, resolve
from rest_framework.test import APIClient

from cms.experiments.s3 import generate_upload_token
from cms.models import Scenario
from shared.api_tokens import scopes
from shared.api_tokens.models import ApiToken
from shared.auth import THREAT_RESEARCH_GROUP

pytestmark = pytest.mark.django_db

SCENARIO_INSTANCES_URL = "/api/v1/cms/experiments/scenarios/basic/instances/"
SCRIPT_UPLOAD_INITIATE_URL = "/api/v1/cms/experiments/scripts/upload/initiate/"
SCRIPT_UPLOAD_COMPLETE_URL = "/api/v1/cms/experiments/scripts/upload/complete/"
YAML_VALIDATE_URL = "/api/v1/cms/scenario-editor/validate-yaml/"
YAML_CREATE_URL = "/api/v1/cms/scenario-editor/scenarios/from-yaml/"
VALID_SCENARIO_YAML = """id: api-created
name: API Created
description: Created through the API
instances:
  - name: Attacker
    role: attacker
    os_type: kali
"""


@pytest.fixture(autouse=True)
def _restore_urlconf() -> None:
    yield
    _reload_urlconfs()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def staff_user(django_user_model):
    return django_user_model.objects.create_user(
        username="cms-api-staff@example.com",
        email="cms-api-staff@example.com",
        is_staff=True,
    )


@pytest.fixture
def threat_research_user(django_user_model):
    user = django_user_model.objects.create_user(
        username="cms-api-threat@example.com",
        email="cms-api-threat@example.com",
    )
    group, _ = Group.objects.get_or_create(name=THREAT_RESEARCH_GROUP)
    user.groups.add(group)
    return user


@pytest.fixture
def regular_user(django_user_model):
    return django_user_model.objects.create_user(
        username="cms-api-regular@example.com",
        email="cms-api-regular@example.com",
    )


def _bearer(client: APIClient, raw: str) -> APIClient:
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _token(user, *granted_scopes: str) -> str:
    _, raw = ApiToken.create_token(name="cms-api", created_by=user, scopes=list(granted_scopes))
    return raw


def _s3_client() -> MagicMock:
    client = MagicMock()
    client.generate_presigned_url.return_value = "https://upload.example.invalid/script.py"
    client.head_object.return_value = {"ContentLength": 12, "ETag": '"etag"'}
    body = MagicMock()
    body.read.return_value = b"print('ok')\n"
    client.get_object.return_value = {"Body": body}
    return client


def _reload_urlconfs() -> None:
    import cms.api.urls
    import config.api_urls
    import config.urls

    clear_url_caches()
    importlib.reload(cms.api.urls)
    importlib.reload(config.api_urls)
    importlib.reload(config.urls)
    clear_url_caches()


class TestCMSExperimentFeatureFlag:
    @override_settings(EXPERIMENTS_ENABLED=False)
    def test_experiment_api_routes_are_absent_when_feature_disabled(self) -> None:
        _reload_urlconfs()

        with pytest.raises(Resolver404):
            resolve(SCENARIO_INSTANCES_URL)

    @override_settings(EXPERIMENTS_ENABLED=True)
    def test_experiment_api_routes_are_registered_when_feature_enabled(self) -> None:
        _reload_urlconfs()

        match = resolve(SCENARIO_INSTANCES_URL)

        assert match.namespace == "v1:cms"
        assert match.url_name == "scenario-instances"


class TestCMSAuthoringReadAccess:
    @override_settings(EXPERIMENTS_ENABLED=True)
    def test_read_token_can_get_scenario_instances(self, api_client: APIClient, staff_user) -> None:
        _reload_urlconfs()
        raw = _token(staff_user, scopes.CMS_AUTHORING_READ)

        response = _bearer(api_client, raw).get(SCENARIO_INSTANCES_URL)

        assert response.status_code == 200
        assert response.json()["instances"] == [
            {"name": "Attacker", "role": "attacker", "os_type": "kali"},
            {"name": "Workstation", "role": "victim", "os_type": "from_agent"},
        ]

    @override_settings(EXPERIMENTS_ENABLED=True)
    def test_token_scope_does_not_bypass_cms_authoring_role(self, api_client: APIClient, regular_user) -> None:
        _reload_urlconfs()
        raw = _token(regular_user, scopes.CMS_AUTHORING_READ)

        response = _bearer(api_client, raw).get(SCENARIO_INSTANCES_URL)

        assert response.status_code == 403

    @override_settings(EXPERIMENTS_ENABLED=True)
    def test_write_scope_does_not_satisfy_read_scope(self, api_client: APIClient, staff_user) -> None:
        _reload_urlconfs()
        raw = _token(staff_user, scopes.CMS_AUTHORING_WRITE)

        response = _bearer(api_client, raw).get(SCENARIO_INSTANCES_URL)

        assert response.status_code == 403


class TestCMSScenarioEditorAPI:
    def test_openapi_schema_includes_scenario_editor_cms_route(self, api_client: APIClient, staff_user) -> None:
        api_client.force_authenticate(user=staff_user)

        response = api_client.get("/api/v1/schema/")

        assert response.status_code == 200
        assert "/api/v1/cms/scenario-editor/validate-yaml/" in response.content.decode()

    def test_session_user_can_validate_yaml(self, api_client: APIClient, threat_research_user) -> None:
        api_client.force_authenticate(user=threat_research_user)

        response = api_client.post(YAML_VALIDATE_URL, {"yaml_content": VALID_SCENARIO_YAML}, format="json")

        assert response.status_code == 200
        payload = response.json()
        assert payload["valid"] is True
        assert payload["errors"] == []
        assert payload["definition"]["id"] == "api-created"

    def test_read_token_can_validate_yaml(self, api_client: APIClient, staff_user) -> None:
        raw = _token(staff_user, scopes.CMS_AUTHORING_READ)

        response = _bearer(api_client, raw).post(
            YAML_VALIDATE_URL,
            {"yaml_content": "name: Missing required fields"},
            format="json",
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["valid"] is False
        assert "Missing required field: id" in payload["errors"]
        assert payload["definition"] is None

    def test_malformed_bearer_fails_closed_over_logged_in_session(self, api_client: APIClient, staff_user) -> None:
        api_client.force_login(staff_user)
        api_client.credentials(HTTP_AUTHORIZATION="Bearer shf_missing.invalid")

        response = api_client.post(YAML_VALIDATE_URL, {"yaml_content": "name: X"}, format="json")

        assert response.status_code == 401

    def test_write_token_can_create_scenario_from_yaml(self, api_client: APIClient, staff_user) -> None:
        raw = _token(staff_user, scopes.CMS_AUTHORING_WRITE)

        response = _bearer(api_client, raw).post(
            YAML_CREATE_URL,
            {"yaml_content": VALID_SCENARIO_YAML},
            format="json",
        )

        assert response.status_code == 201
        assert response.json() == {"scenario_id": "api-created", "name": "API Created"}
        assert Scenario.objects.filter(scenario_id="api-created", created_by=staff_user).exists()

    def test_read_token_cannot_create_scenario_from_yaml(self, api_client: APIClient, staff_user) -> None:
        raw = _token(staff_user, scopes.CMS_AUTHORING_READ)

        response = _bearer(api_client, raw).post(
            YAML_CREATE_URL,
            {"yaml_content": VALID_SCENARIO_YAML.replace("api-created", "api-read-denied")},
            format="json",
        )

        assert response.status_code == 403
        assert not Scenario.objects.filter(scenario_id="api-read-denied").exists()


class TestCMSScriptUploadAPI:
    @override_settings(
        EXPERIMENTS_ENABLED=True,
        CLOUD_PROVIDER="aws",
        AWS_S3_BUCKET_NAME="test-bucket",
        SCRIPT_UPLOAD_URL_EXPIRES=600,
        SCRIPT_MAX_FILE_SIZE_BYTES=1024,
    )
    def test_write_token_can_initiate_script_upload(self, api_client: APIClient, staff_user) -> None:
        _reload_urlconfs()
        raw = _token(staff_user, scopes.CMS_AUTHORING_WRITE)
        s3_client = _s3_client()

        with patch("boto3.client", return_value=s3_client):
            response = _bearer(api_client, raw).post(
                SCRIPT_UPLOAD_INITIATE_URL,
                {"name": "Script", "filename": "script.py", "file_size": 12},
                format="json",
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["presigned_url"] == "https://upload.example.invalid/script.py"
        assert payload["s3_key"].startswith(f"scripts/{staff_user.pk}/")
        assert payload["s3_key"].endswith("_script.py")
        assert payload["upload_token"]
        s3_client.generate_presigned_url.assert_called_once()

    @override_settings(
        EXPERIMENTS_ENABLED=True,
        CLOUD_PROVIDER="aws",
        AWS_S3_BUCKET_NAME="test-bucket",
        SCRIPT_UPLOAD_URL_EXPIRES=600,
        SCRIPT_MAX_FILE_SIZE_BYTES=1024,
    )
    def test_write_token_can_complete_script_upload(self, api_client: APIClient, staff_user) -> None:
        _reload_urlconfs()
        raw = _token(staff_user, scopes.CMS_AUTHORING_WRITE)
        s3_key = f"scripts/{staff_user.pk}/api_script.py"
        upload_token = generate_upload_token(
            user_id=staff_user.pk,
            s3_key=s3_key,
            name="Script",
            filename="script.py",
            file_size=12,
        )
        s3_client = _s3_client()

        with patch("boto3.client", return_value=s3_client):
            response = _bearer(api_client, raw).post(
                SCRIPT_UPLOAD_COMPLETE_URL,
                {"upload_token": upload_token},
                format="json",
            )

        assert response.status_code == 200
        script = response.json()["script"]
        assert script["name"] == "Script"
        assert script["original_filename"] == "script.py"
        assert script["file_size_bytes"] == 12
        s3_client.head_object.assert_called_once()
        s3_client.get_object.assert_called_once()

    @override_settings(EXPERIMENTS_ENABLED=True)
    def test_script_upload_rejects_read_scope(self, api_client: APIClient, staff_user) -> None:
        _reload_urlconfs()
        raw = _token(staff_user, scopes.CMS_AUTHORING_READ)

        response = _bearer(api_client, raw).post(
            SCRIPT_UPLOAD_INITIATE_URL,
            {"name": "Script", "filename": "script.py", "file_size": 12},
            format="json",
        )

        assert response.status_code == 403
