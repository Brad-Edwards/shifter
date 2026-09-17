"""Tests for the per-range Vertex agent credential lifecycle."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gcp_range_vertex_creds import delete_range_vertex_key


class NotFound(Exception):
    """Fake google.api_core NotFound."""


class AlreadyExists(Exception):
    """Fake google.api_core AlreadyExists."""


_EXCEPTIONS = SimpleNamespace(NotFound=NotFound, AlreadyExists=AlreadyExists)

_KEY_JSON = json.dumps(
    {
        "type": "service_account",
        "client_email": "range-vertex@proj.iam.gserviceaccount.com",
        "private_key_id": "abc123",
    }
)


@pytest.fixture(autouse=True)
def _explicit_dynamic_secret_project(monkeypatch):
    """Exercise the supported same-project migration posture explicitly."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("GCP_DYNAMIC_SECRET_PROJECT_ID", "proj")


def _secret_client(*, exists: bool):
    client = SimpleNamespace()
    if exists:
        client.access_secret_version = lambda *, request: SimpleNamespace(
            payload=SimpleNamespace(data=_KEY_JSON.encode("utf-8"))
        )
    else:

        def _raise(*, request):
            raise NotFound()

        client.access_secret_version = _raise

    def _get_secret(*, request):
        raise NotFound()

    client.get_secret = _get_secret
    client.create_secret = lambda *, request: None
    client.add_secret_version = lambda *, request: None
    client.get_iam_policy = lambda *, request: {"bindings": []}
    client.set_iam_policy = lambda *, request: None
    client.delete_secret = lambda *, request: None
    return client


def test_delete_removes_key_and_secret(mocker):
    iam = SimpleNamespace(delete_service_account_key=mocker.Mock())
    secrets = _secret_client(exists=True)
    delete_secret = mocker.spy(secrets, "delete_secret")

    delete_range_vertex_key(
        42,
        iam_client=iam,
        secret_client=secrets,
        google_exceptions=_EXCEPTIONS,
        project_id="proj",
    )

    iam.delete_service_account_key.assert_called_once_with(
        request={"name": "projects/-/serviceAccounts/range-vertex@proj.iam.gserviceaccount.com/keys/abc123"}
    )
    delete_secret.assert_called_once()


def test_delete_revokes_keys_from_both_legacy_and_canonical_migration_locations(mocker, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "gcp-dev")
    monkeypatch.setenv("GCP_DYNAMIC_SECRET_PROJECT_ID", "range-secrets")
    legacy_key = _KEY_JSON.encode()
    canonical_key = json.dumps(
        {
            "type": "service_account",
            "client_email": "range-vertex@proj.iam.gserviceaccount.com",
            "private_key_id": "def456",
        }
    ).encode()
    iam = SimpleNamespace(delete_service_account_key=mocker.Mock())
    secrets = _secret_client(exists=True)
    secrets.access_secret_version = mocker.Mock(
        side_effect=[
            SimpleNamespace(payload=SimpleNamespace(data=legacy_key)),
            SimpleNamespace(payload=SimpleNamespace(data=canonical_key)),
        ]
    )
    delete_secret = mocker.spy(secrets, "delete_secret")

    delete_range_vertex_key(
        42,
        iam_client=iam,
        secret_client=secrets,
        google_exceptions=_EXCEPTIONS,
        project_id="proj",
    )

    assert {call.kwargs["request"]["name"] for call in iam.delete_service_account_key.call_args_list} == {
        "projects/-/serviceAccounts/range-vertex@proj.iam.gserviceaccount.com/keys/abc123",
        "projects/-/serviceAccounts/range-vertex@proj.iam.gserviceaccount.com/keys/def456",
    }
    assert {call.kwargs["request"]["name"] for call in delete_secret.call_args_list} == {
        "projects/proj/secrets/shifter-range-42-vertex-key",
        "projects/range-secrets/secrets/shifter-gcp-dev-dynamic-workload-vertex-range-42-service-account-key",
    }


def test_delete_is_noop_when_secret_absent(mocker):
    iam = SimpleNamespace(delete_service_account_key=mocker.Mock())
    secrets = _secret_client(exists=False)

    delete_range_vertex_key(
        42,
        iam_client=iam,
        secret_client=secrets,
        google_exceptions=_EXCEPTIONS,
        project_id="proj",
    )

    iam.delete_service_account_key.assert_not_called()


def test_delete_shared_key_mode_removes_only_range_secret(mocker, monkeypatch):
    monkeypatch.setenv("GCP_RANGE_VERTEX_SHARED_KEY_SECRET_ID", "shared-vertex-key")
    iam = SimpleNamespace(delete_service_account_key=mocker.Mock())
    secrets = _secret_client(exists=True)
    access_secret = mocker.spy(secrets, "access_secret_version")
    delete_secret = mocker.spy(secrets, "delete_secret")

    delete_range_vertex_key(
        42,
        iam_client=iam,
        secret_client=secrets,
        google_exceptions=_EXCEPTIONS,
        project_id="proj",
    )

    access_secret.assert_not_called()
    iam.delete_service_account_key.assert_not_called()
    delete_secret.assert_called_once_with(request={"name": "projects/proj/secrets/shifter-range-42-vertex-key"})
