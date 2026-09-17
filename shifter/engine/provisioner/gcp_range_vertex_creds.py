"""Revoke legacy range provider credentials during teardown; never issue new keys."""

from __future__ import annotations

import json
import logging
import os
from contextlib import suppress
from typing import Protocol

from cloud.gcp.base import get_project_id, import_google_module
from gcp_dynamic_secrets import (
    DynamicSecretClass,
    SecretLocations,
    canonical_secret_id,
    delete_all,
    dynamic_secret_project_id,
    secret_locations,
)
from log_redact import safe_log_fingerprint

logger = logging.getLogger(__name__)

_IAM_ADMIN_MODULE = "google.cloud.iam_admin_v1"
_SECRETMANAGER_MODULE = "google.cloud.secretmanager"
_GOOGLE_EXCEPTIONS_MODULE = "google.api_core.exceptions"


class _SecretPayload(Protocol):
    """Secret Manager payload subset."""

    data: bytes


class _SecretVersion(Protocol):
    """Secret Manager version response subset."""

    payload: _SecretPayload


class _IamClient(Protocol):
    """IAM Admin client subset used to revoke legacy range Vertex keys."""

    def delete_service_account_key(self, *, request: dict[str, object]) -> object:
        """Delete a service-account key."""


class _SecretClient(Protocol):
    """Secret Manager client subset used to store the range Vertex key."""

    def access_secret_version(self, *, request: dict[str, object]) -> _SecretVersion:
        """Return the latest secret version."""

    def get_secret(self, *, request: dict[str, object]) -> object:
        """Return an existing secret container."""

    def delete_secret(self, *, request: dict[str, object]) -> object:
        """Delete a Secret Manager secret."""


class _GoogleExceptions(Protocol):
    """Google exception module subset used by the Vertex credential path."""

    NotFound: type[Exception]
    AlreadyExists: type[Exception]


def _vertex_secret_id(range_id: int) -> str:
    """Return the deterministic Secret Manager id for a range's Vertex key."""
    return f"shifter-range-{int(range_id)}-vertex-key"


def _resolve_project_id(project_id: str | None) -> str:
    """Resolve the active GCP project id, requiring one to be available."""
    resolved = project_id or get_project_id()
    if not resolved:
        raise RuntimeError("GCP project ID is required to manage range Vertex credentials")
    return resolved


def _build_iam_client() -> _IamClient:
    """Build the IAM Admin client used to revoke legacy Vertex keys."""
    return import_google_module(_IAM_ADMIN_MODULE).IAMClient()


def _build_secret_client() -> _SecretClient:
    """Build the Secret Manager client used to store the Vertex key."""
    return import_google_module(_SECRETMANAGER_MODULE).SecretManagerServiceClient()


def _google_exceptions() -> _GoogleExceptions:
    """Return the Google API exception module (NotFound / AlreadyExists)."""
    return import_google_module(_GOOGLE_EXCEPTIONS_MODULE)


def _shared_secret_id() -> str:
    """Return the configured shared Vertex key secret id (empty when unset).

    When set, ranges copy the key from this one shared secret instead of minting
    a fresh per-range SA key, which avoids service-account key quota pressure on
    deployments that provision the shared key out-of-band.
    """
    return os.environ.get("GCP_RANGE_VERTEX_SHARED_KEY_SECRET_ID", "").strip()


def _vertex_secret_locations(platform_project_id: str, range_id: int) -> SecretLocations:
    """Resolve legacy and canonical locations for a range Vertex key."""
    return secret_locations(
        platform_project_id=platform_project_id,
        dynamic_project_id=dynamic_secret_project_id(),
        legacy_secret_id=_vertex_secret_id(range_id),
        canonical_secret_id=canonical_secret_id(
            credential_class=DynamicSecretClass.VERTEX_SERVICE_ACCOUNT_KEY,
            scope=f"range-{range_id}",
        ),
    )


def _stored_vertex_key_names(
    secrets: _SecretClient,
    exceptions: _GoogleExceptions,
    locations: SecretLocations,
) -> set[str]:
    """Collect the distinct SA key names referenced by all migration locations."""
    key_names: set[str] = set()
    for secret_name in locations.read_refs:
        try:
            response = secrets.access_secret_version(request={"name": f"{secret_name}/versions/latest"})
        except exceptions.NotFound:
            continue
        if key_name := _key_resource_name(response.payload.data):
            key_names.add(key_name)
    return key_names


def _delete_vertex_keys(
    key_names: set[str],
    iam: _IamClient,
    exceptions: _GoogleExceptions,
) -> None:
    """Delete collected service-account keys, ignoring already-absent keys."""
    for key_name in sorted(key_names):
        with suppress(exceptions.NotFound):
            iam.delete_service_account_key(request={"name": key_name})
            logger.info("Deleted range Vertex SA key key_fp=%s", safe_log_fingerprint(key_name))


def delete_range_vertex_key(
    range_id: int,
    *,
    iam_client: _IamClient | None = None,
    secret_client: _SecretClient | None = None,
    google_exceptions: _GoogleExceptions | None = None,
    project_id: str | None = None,
) -> None:
    """Delete a range's Vertex SA key and its secret, ignoring missing resources."""
    try:
        resolved_project = _resolve_project_id(project_id)
    except RuntimeError:
        return
    exceptions = google_exceptions or _google_exceptions()
    secrets = secret_client or _build_secret_client()
    locations = _vertex_secret_locations(resolved_project, range_id)

    shared_secret_id = _shared_secret_id()

    key_names = set() if shared_secret_id else _stored_vertex_key_names(secrets, exceptions, locations)

    if key_names:
        _delete_vertex_keys(key_names, iam_client or _build_iam_client(), exceptions)

    delete_all(secrets, exceptions, locations)
    logger.info(
        "Deleted range Vertex key secret locations secret_fp=%s",
        safe_log_fingerprint("|".join(locations.delete_refs)),
    )


def _key_resource_name(payload_data: bytes) -> str:
    """Reconstruct the SA key resource name from stored key JSON."""
    try:
        parsed = json.loads(payload_data.decode("utf-8") if isinstance(payload_data, bytes) else str(payload_data))
    except (ValueError, AttributeError):
        return ""
    email = str(parsed.get("client_email", "")).strip()
    key_id = str(parsed.get("private_key_id", "")).strip()
    if not email or not key_id:
        return ""
    return f"projects/-/serviceAccounts/{email}/keys/{key_id}"
