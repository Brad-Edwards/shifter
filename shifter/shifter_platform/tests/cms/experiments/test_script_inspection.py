"""Behavior tests for server-side header inspection in complete_script_upload (#696).

Drives the real ``complete_script_upload`` with a real signed upload token and S3
exercised through the real ``cms.experiments.s3`` helpers + ``shared.cloud`` AWS
adapter, mocked only at the ``boto3`` boundary — instead of patching
``verify_upload_token`` / ``verify_s3_object`` / ``read_script_header`` /
``delete_s3_object`` / ``ScriptAsset`` / ``audit_log`` / ``transaction`` /
``_check_result_type``. Asserts the persisted ``ScriptAsset`` / ``AuditLog`` on
accept, and the delete-and-reject behavior on binary / non-UTF-8 / size-mismatch.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model

from cms.experiments import services
from cms.experiments.exceptions import ScriptUploadError
from cms.experiments.models import ScriptAsset
from cms.experiments.s3 import generate_upload_token
from risk_register.models import AuditLog

pytestmark = pytest.mark.django_db

User = get_user_model()

S3_KEY = "scripts/inspect/abc_script.py"


@pytest.fixture
def user(db):
    return User.objects.create_user(username="scripter@e.com", email="scripter@e.com", is_staff=True)


@pytest.fixture
def s3_client(settings):
    """boto3 S3 client mock: head returns 100 bytes; get_object returns valid text."""
    settings.CLOUD_PROVIDER = "aws"
    settings.AWS_S3_BUCKET_NAME = "test-bucket"
    settings.SCRIPT_UPLOAD_URL_EXPIRES = 600
    settings.SCRIPT_MAX_FILE_SIZE_BYTES = 1024
    client = MagicMock()
    client.head_object.return_value = {"ContentLength": 100, "ETag": '"etag"'}
    _set_body(client, b"print('ok')\n")
    with patch("boto3.client", return_value=client):
        yield client


def _set_body(s3_client, data: bytes):
    body = MagicMock()
    body.read.return_value = data
    s3_client.get_object.return_value = {"Body": body}


def _token(user, *, file_size=100, name="My Script", filename="script.py"):
    return generate_upload_token(user_id=user.id, s3_key=S3_KEY, name=name, filename=filename, file_size=file_size)


class TestHappyPath:
    def test_text_header_accepted_then_script_saved(self, user, s3_client):
        _set_body(s3_client, b"#!/usr/bin/env python3\nprint('ok')\n")
        script = services.complete_script_upload(user, _token(user))

        persisted = ScriptAsset.objects.get(pk=script.pk)
        assert persisted.name == "My Script"
        assert persisted.s3_key == S3_KEY
        assert persisted.user_id == user.id
        s3_client.delete_object.assert_not_called()
        assert AuditLog.objects.filter(
            entity_type=AuditLog.EntityType.SCRIPT, entity_id=script.pk, action=AuditLog.Action.CREATE
        ).exists()


class TestBomAccepted:
    def test_bom_prefixed_python_accepted(self, user, s3_client):
        _set_body(s3_client, b"\xef\xbb\xbfprint('hi')\n")
        script = services.complete_script_upload(user, _token(user))

        assert ScriptAsset.objects.filter(pk=script.pk).exists()
        s3_client.delete_object.assert_not_called()


class TestBinaryHeaderRejected:
    def test_zip_magic_rejected_and_object_deleted(self, user, s3_client):
        _set_body(s3_client, b"\x50\x4b\x03\x04zip-bytes-pretending-to-be-py")
        with pytest.raises(ScriptUploadError, match="content"):
            services.complete_script_upload(user, _token(user))

        assert s3_client.delete_object.call_args.kwargs["Key"] == S3_KEY
        assert ScriptAsset.objects.count() == 0
        assert not AuditLog.objects.filter(entity_type=AuditLog.EntityType.SCRIPT).exists()

    def test_elf_header_rejected_and_object_deleted(self, user, s3_client):
        _set_body(s3_client, b"\x7fELF\x02\x01\x01\x00not-python")
        with pytest.raises(ScriptUploadError):
            services.complete_script_upload(user, _token(user))

        assert s3_client.delete_object.call_args.kwargs["Key"] == S3_KEY
        assert ScriptAsset.objects.count() == 0


class TestNonUtf8Rejected:
    def test_non_utf8_header_rejected_and_object_deleted(self, user, s3_client):
        _set_body(s3_client, b"\xff\xfe\xfd\xfc garbage")
        with pytest.raises(ScriptUploadError):
            services.complete_script_upload(user, _token(user))

        assert s3_client.delete_object.call_args.kwargs["Key"] == S3_KEY
        assert ScriptAsset.objects.count() == 0


class TestHeaderReadFailure:
    def test_header_read_s3_error_surfaces_and_does_not_delete(self, user, s3_client):
        s3_client.get_object.side_effect = ClientError(
            {"Error": {"Code": "500", "Message": "range failed"}}, "GetObject"
        )
        with pytest.raises(ScriptUploadError):
            services.complete_script_upload(user, _token(user))

        # Transport failure (not a content mismatch) must NOT auto-delete the object.
        s3_client.delete_object.assert_not_called()
        assert ScriptAsset.objects.count() == 0


class TestSizeMismatch:
    def test_size_mismatch_rejects_and_deletes_object(self, user, s3_client):
        # Token claims 100 bytes; S3 reports 500 -> reject before inspection.
        s3_client.head_object.return_value = {"ContentLength": 500, "ETag": '"etag"'}
        with pytest.raises(ScriptUploadError, match="size mismatch"):
            services.complete_script_upload(user, _token(user, file_size=100))

        assert s3_client.delete_object.call_args.kwargs["Key"] == S3_KEY
        s3_client.get_object.assert_not_called()  # header not read once size mismatched
        assert ScriptAsset.objects.count() == 0


class TestFullBodyScan:
    def test_full_body_is_read_using_max_script_size(self, user, s3_client, settings):
        settings.SCRIPT_MAX_FILE_SIZE_BYTES = 1024
        _set_body(s3_client, b"print('hello')\n")
        services.complete_script_upload(user, _token(user))

        # read_object_header issues a Range GET for the full max size (end-inclusive).
        assert s3_client.get_object.call_args.kwargs["Range"] == "bytes=0-1023"

    def test_text_prefix_with_binary_tail_rejected(self, user, s3_client):
        # Valid text prefix followed by binary garbage: the full-body scan catches it.
        _set_body(s3_client, b"# innocuous Python prefix\n" + b"\xff\xfe binary garbage\n")
        with pytest.raises(ScriptUploadError):
            services.complete_script_upload(user, _token(user))

        assert s3_client.delete_object.call_args.kwargs["Key"] == S3_KEY


class TestLoggingDiscipline:
    def test_rejection_log_does_not_leak_header_bytes_or_token(self, user, s3_client, caplog):
        _set_body(s3_client, b"\x50\x4b\x03\x04S3CR3T-DO-NOT-LEAK")
        token = _token(user)
        with caplog.at_level("WARNING", logger="cms.experiments.services"), pytest.raises(ScriptUploadError):
            services.complete_script_upload(user, token)

        combined = " ".join(record.getMessage() for record in caplog.records)
        assert "S3CR3T-DO-NOT-LEAK" not in combined
        assert token not in combined
