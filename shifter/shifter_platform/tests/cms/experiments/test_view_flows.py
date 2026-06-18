"""Behavior tests for the script-upload, script-delete, and artifact-download views.

Drives the real views through the Django test client against real
``ScriptAsset`` / ``Experiment`` / ``ExperimentRun`` / ``RunArtifact`` /
``ExperimentArtifact`` rows, the real services, and S3 mocked only at the
``boto3`` boundary — instead of ``RequestFactory`` + mocking the service layer at
source. The experiment create/detail/start/cancel branches and generic
unexpected-exception redirects from the old mock-coupled suite are covered (or
intentionally out of scope) elsewhere; this file focuses on the script/download
flows.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from cms.experiments.models import Experiment, ExperimentArtifact, ExperimentRun, RunArtifact, ScriptAsset
from cms.experiments.s3 import generate_upload_token

pytestmark = pytest.mark.django_db

User = get_user_model()

SCRIPT_UPLOAD_URL = reverse("experiments:script_upload")
SCRIPT_LIST_URL = reverse("experiments:script_list")
PRESIGNED = "https://s3.example/presigned"


@pytest.fixture
def staff(db):
    return User.objects.create_user(username="vf-staff@e.com", email="vf-staff@e.com", is_staff=True)


@pytest.fixture
def client(authenticated_client, staff):
    c, _ = authenticated_client(user=staff)
    return c


@pytest.fixture
def boto3_client(settings):
    """boto3 mock for S3 presign + head + get_object header inspection."""
    settings.CLOUD_PROVIDER = "aws"
    settings.AWS_S3_BUCKET_NAME = "test-bucket"
    settings.SCRIPT_UPLOAD_URL_EXPIRES = 600
    settings.SCRIPT_MAX_FILE_SIZE_BYTES = 1024
    client = MagicMock()
    client.generate_presigned_url.return_value = PRESIGNED
    client.head_object.return_value = {"ContentLength": 12, "ETag": '"etag"'}
    body = MagicMock()
    body.read.return_value = b"print('x')\n"
    client.get_object.return_value = {"Body": body}
    with patch("boto3.client", return_value=client):
        yield client


def _experiment(user):
    return Experiment.objects.create(user=user, name="VF Exp", scenario_id="basic")


class TestScriptUploadView:
    def test_get_renders_form(self, client):
        assert client.get(SCRIPT_UPLOAD_URL).status_code == 200

    def test_put_method_not_allowed(self, client):
        assert client.put(SCRIPT_UPLOAD_URL).status_code == 405

    def test_post_initiate_returns_presigned_json(self, client, boto3_client):
        resp = client.post(SCRIPT_UPLOAD_URL, data={"name": "Demo", "filename": "demo.py", "file_size": "10"})
        assert resp.status_code == 200
        assert json.loads(resp.content)["presigned_url"] == PRESIGNED

    def test_post_initiate_invalid_size_returns_400(self, client):
        resp = client.post(SCRIPT_UPLOAD_URL, data={"name": "Demo", "filename": "demo.py", "file_size": "abc"})
        assert resp.status_code == 400

    def test_post_complete_creates_script(self, staff, client, boto3_client):
        token = generate_upload_token(
            user_id=staff.id, s3_key="scripts/vf/demo.py", name="Demo", filename="demo.py", file_size=12
        )
        resp = client.post(SCRIPT_UPLOAD_URL, data={"upload_token": token})
        assert resp.status_code == 302
        assert ScriptAsset.objects.filter(user=staff, name="Demo").exists()

    def test_post_complete_invalid_token_redirects(self, client, boto3_client):
        resp = client.post(SCRIPT_UPLOAD_URL, data={"upload_token": "not-a-valid-token"})
        assert resp.status_code == 302
        assert ScriptAsset.objects.count() == 0


class TestScriptDeleteView:
    def test_delete_soft_deletes_script(self, staff, client):
        script = ScriptAsset.objects.create(
            user=staff, name="Doomed", s3_key="scripts/vf/d.py", original_filename="d.py", file_size_bytes=10
        )
        resp = client.post(reverse("experiments:script_delete", kwargs={"script_id": script.pk}))
        assert resp.status_code == 302
        script.refresh_from_db()
        assert script.deleted_at is not None

    def test_delete_other_users_script_redirects(self, staff, client):
        other = User.objects.create_user(username="vf-other@e.com", email="vf-other@e.com", is_staff=True)
        script = ScriptAsset.objects.create(
            user=other, name="Theirs", s3_key="scripts/o/d.py", original_filename="d.py", file_size_bytes=10
        )
        resp = client.post(reverse("experiments:script_delete", kwargs={"script_id": script.pk}))
        assert resp.status_code == 302  # ScriptUploadError surfaced as a message + redirect
        script.refresh_from_db()
        assert script.deleted_at is None


class TestExperimentDownloadView:
    def test_download_redirects_to_presigned_bundle_url(self, staff, client, boto3_client):
        exp = _experiment(staff)
        ExperimentArtifact.objects.create(experiment=exp, s3_key="experiments/vf/bundle.zip", file_size_bytes=1024)

        resp = client.get(reverse("experiments:experiment_download", kwargs={"experiment_id": exp.pk}))
        assert resp.status_code == 302
        assert resp["Location"] == PRESIGNED

    def test_download_missing_bundle_redirects_to_detail(self, staff, client, boto3_client):
        exp = _experiment(staff)  # no bundle
        resp = client.get(reverse("experiments:experiment_download", kwargs={"experiment_id": exp.pk}))
        assert resp.status_code == 302
        assert resp["Location"] == reverse("experiments:experiment_detail", kwargs={"experiment_id": exp.pk})


class TestArtifactDownloadView:
    def _artifact(self, exp):
        run = ExperimentRun.objects.create(experiment=exp, run_number=1)
        artifact = RunArtifact.objects.create(
            run=run, instance_name="Attacker", artifact_type="claude_transcript", s3_key="experiments/vf/a.txt"
        )
        return run, artifact

    def test_download_redirects_to_presigned_url(self, staff, client, boto3_client):
        exp = _experiment(staff)
        run, artifact = self._artifact(exp)

        resp = client.get(
            reverse(
                "experiments:artifact_download",
                kwargs={"experiment_id": exp.pk, "run_number": run.run_number, "artifact_id": artifact.pk},
            )
        )
        assert resp.status_code == 302
        assert resp["Location"] == PRESIGNED

    def test_download_missing_artifact_redirects_to_detail(self, staff, client, boto3_client):
        exp = _experiment(staff)
        resp = client.get(
            reverse(
                "experiments:artifact_download",
                kwargs={"experiment_id": exp.pk, "run_number": 1, "artifact_id": 999999},
            )
        )
        assert resp.status_code == 302
        assert resp["Location"] == reverse("experiments:experiment_detail", kwargs={"experiment_id": exp.pk})
