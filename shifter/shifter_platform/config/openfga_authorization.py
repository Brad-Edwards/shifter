"""Official OpenFGA SDK binding for Shifter authorization contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from openfga_sdk import ReadRequestTupleKey
from openfga_sdk.client import ClientConfiguration
from openfga_sdk.client.models import (
    ClientBatchCheckItem,
    ClientBatchCheckRequest,
    ClientCheckRequest,
    ClientReadChangesRequest,
    ClientTuple,
    ClientWriteRequest,
)
from openfga_sdk.client.models.write_conflict_opts import (
    ClientWriteRequestOnDuplicateWrites,
    ClientWriteRequestOnMissingDeletes,
    ConflictOptions,
)
from openfga_sdk.configuration import RetryParams
from openfga_sdk.credentials import CredentialConfiguration, Credentials
from openfga_sdk.sync import OpenFgaClient

from shared.authorization import (
    AuthorizationDecision,
    AuthorizationProviderError,
    AuthorizationRelationshipChange,
    AuthorizationRequest,
    DecisionKind,
    PolicyEffect,
    RelationshipChangePage,
    RelationshipObjectType,
    RelationshipState,
    VersionedRelationshipChange,
)
from shared.authorization.model import decision_relation_for_action

_CONSISTENCY_OPTIONS = {"consistency": "HIGHER_CONSISTENCY"}
_MAX_SECRET_BYTES = 16_384


@dataclass(frozen=True, slots=True)
class OpenFgaRuntimeSettings:
    """Validated private runtime binding; store/model administration is absent."""

    api_url: str
    store_id: str
    model_id: str
    api_token: str = field(repr=False)
    ca_cert_path: str
    timeout_ms: int = 1500

    def __post_init__(self) -> None:
        parsed = urlparse(self.api_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OpenFGA api_url must use https")
        if not self.api_token:
            raise ValueError("OpenFGA runtime API token is required")
        if not re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", self.store_id):
            raise ValueError("OpenFGA store id is required")
        if not re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", self.model_id):
            raise ValueError("OpenFGA model id is required")
        if not self.ca_cert_path:
            raise ValueError("OpenFGA CA certificate path is required")
        if not 100 <= self.timeout_ms <= 10_000:
            raise ValueError("OpenFGA timeout must be between 100 and 10000 milliseconds")


def _sdk_client(settings: OpenFgaRuntimeSettings) -> OpenFgaClient:
    credentials = Credentials(
        method="api_token",
        configuration=CredentialConfiguration(api_token=settings.api_token),
    )
    configuration = ClientConfiguration(
        api_url=settings.api_url,
        store_id=settings.store_id,
        authorization_model_id=settings.model_id,
        credentials=credentials,
        ssl_ca_cert=settings.ca_cert_path,
        timeout_millisec=settings.timeout_ms,
        # The durable operation layer owns write retry and reconciliation.
        retry_params=RetryParams(max_retry=0),
    )
    return OpenFgaClient(configuration)


def _read_secret_file(path_value: str) -> str:
    if not path_value:
        raise ImproperlyConfigured("OPENFGA_API_TOKEN_FILE is required when OpenFGA is enabled")
    path = Path(path_value)
    try:
        size = path.stat().st_size
        if size <= 0 or size > _MAX_SECRET_BYTES:
            raise ImproperlyConfigured("OpenFGA API token file has an invalid size")
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ImproperlyConfigured("OpenFGA API token file is unavailable") from exc
    if not value:
        raise ImproperlyConfigured("OpenFGA API token file is empty")
    return value


def runtime_settings_from_django() -> OpenFgaRuntimeSettings:
    """Build a validated runtime binding from non-secret settings and a secret file."""
    if not django_settings.OPENFGA_ENABLED:
        raise ImproperlyConfigured("OpenFGA authorization is not enabled")
    try:
        return OpenFgaRuntimeSettings(
            api_url=django_settings.OPENFGA_API_URL,
            store_id=django_settings.OPENFGA_STORE_ID,
            model_id=django_settings.OPENFGA_MODEL_ID,
            api_token=_read_secret_file(django_settings.OPENFGA_API_TOKEN_FILE),
            ca_cert_path=django_settings.OPENFGA_CA_CERT_PATH,
            timeout_ms=django_settings.OPENFGA_TIMEOUT_MS,
        )
    except ValueError as exc:
        raise ImproperlyConfigured("OpenFGA runtime settings are invalid") from exc


def configured_authorization_provider() -> OpenFgaAuthorizationProvider:
    """Composition-root provider factory; intentionally unavailable while S2 is disabled."""
    return OpenFgaAuthorizationProvider(runtime_settings_from_django())


def _provider_object(request: AuthorizationRequest) -> str:
    if request.target.type == "installation":
        return "installation:root"
    return f"{request.target.type}:{request.target.uuid}"


def _check_body(request: AuthorizationRequest) -> ClientCheckRequest:
    return ClientCheckRequest(
        user=f"principal:{request.principal.uuid}",
        relation=decision_relation_for_action(request.action),
        object=_provider_object(request),
    )


def _decision(allowed: bool) -> AuthorizationDecision:
    if allowed:
        return AuthorizationDecision(DecisionKind.ALLOWED, "policy_allowed")
    return AuthorizationDecision(DecisionKind.DENIED, "policy_denied")


def _scope_parents(request: AuthorizationRequest) -> tuple[tuple[str, str, str], ...]:
    """Expected provider edges derived only from the trusted SQL scope."""
    scope = request.scope
    if scope.kind == "installation":
        return ()
    edges = [(f"account:{scope.account_uuid}", "installation", "installation:root")]
    if scope.organization_uuid is not None:
        edges.append((f"organization:{scope.organization_uuid}", "account", f"account:{scope.account_uuid}"))
    if scope.workspace_uuid is not None:
        edges.append((f"workspace:{scope.workspace_uuid}", "organization", f"organization:{scope.organization_uuid}"))
    if request.target.type in {"event", "range"}:
        if scope.workspace_uuid is None:
            raise AuthorizationProviderError("Resource ancestry is unavailable")
        if request.target.type == "event":
            edges.append((_provider_object(request), "workspace", f"workspace:{scope.workspace_uuid}"))
    return tuple(edges)


class OpenFgaAuthorizationProvider:
    """Fail-closed primary-backed evaluator using only the supported SDK surface."""

    def __init__(self, settings: OpenFgaRuntimeSettings, *, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client if client is not None else _sdk_client(settings)

    @property
    def store_id(self) -> str:
        return self._settings.store_id

    @property
    def model_id(self) -> str:
        return self._settings.model_id

    @property
    def _options(self) -> dict[str, str]:
        return {"authorization_model_id": self._settings.model_id, **_CONSISTENCY_OPTIONS}

    def _parents(self, object_id: str, relation: str) -> tuple[str, ...]:
        response = self._client.read(
            ReadRequestTupleKey(object=object_id, relation=relation),
            {**self._options, "page_size": 2},
        )
        if getattr(response, "continuation_token", ""):
            raise AuthorizationProviderError("Ambiguous resource ancestry")
        return tuple(item.key.user for item in response.tuples)

    def _ancestry_matches(self, request: AuthorizationRequest) -> bool:
        for object_id, relation, parent in _scope_parents(request):
            if self._parents(object_id, relation) != (parent,):
                return False
        if request.target.type == "range":
            workspace = f"workspace:{request.scope.workspace_uuid}"
            workspaces = self._parents(_provider_object(request), "workspace")
            if workspaces not in {(), (workspace,)}:
                return False
            events = self._parents(_provider_object(request), "event")
            if len(events) > 1 or not (workspaces or events):
                return False
            if events and self._parents(events[0], "workspace") != (workspace,):
                return False
        return True

    def check(self, request: AuthorizationRequest) -> AuthorizationDecision:
        try:
            if not self._ancestry_matches(request):
                return AuthorizationDecision(DecisionKind.DENIED, "invalid_request")
            response = self._client.check(_check_body(request), self._options)
            return _decision(response.allowed is True)
        except Exception:
            return AuthorizationDecision(DecisionKind.EVALUATOR_ERROR, "evaluator_unavailable")

    def batch_check(self, requests: Sequence[AuthorizationRequest]) -> tuple[AuthorizationDecision, ...]:
        if not requests:
            return ()
        checks = [
            ClientBatchCheckItem(
                user=f"principal:{request.principal.uuid}",
                relation=decision_relation_for_action(request.action),
                object=_provider_object(request),
                correlation_id=str(index),
            )
            for index, request in enumerate(requests)
        ]
        failed = AuthorizationDecision(DecisionKind.EVALUATOR_ERROR, "incomplete_batch")
        try:
            checked_targets = set()
            for request in requests:
                identity = (request.target, request.scope)
                if identity not in checked_targets:
                    if not self._ancestry_matches(request):
                        return tuple(AuthorizationDecision(DecisionKind.DENIED, "invalid_request") for _ in requests)
                    checked_targets.add(identity)
            response = self._client.batch_check(ClientBatchCheckRequest(checks=checks), self._options)
            results = response.result
            if isinstance(results, Mapping):
                by_correlation = dict(results)
            elif isinstance(results, list):
                by_correlation = {getattr(item, "correlation_id", None): item for item in results}
            else:
                return tuple(failed for _ in requests)
            expected = {str(index) for index in range(len(requests))}
            if len(by_correlation) != len(results) or set(by_correlation) != expected:
                return tuple(failed for _ in requests)
            ordered = [by_correlation[str(index)] for index in range(len(requests))]
            if any(getattr(item, "error", None) is not None for item in ordered):
                return tuple(failed for _ in requests)
            return tuple(_decision(getattr(item, "allowed", False) is True) for item in ordered)
        except Exception:
            return tuple(AuthorizationDecision(DecisionKind.EVALUATOR_ERROR, "evaluator_unavailable") for _ in requests)

    def write_relationships(
        self,
        change: AuthorizationRelationshipChange | VersionedRelationshipChange,
    ) -> None:
        body = ClientWriteRequest(
            writes=[ClientTuple(item.user, item.relation, item.object) for item in change.writes],
            deletes=[ClientTuple(item.user, item.relation, item.object) for item in change.deletes],
        )
        options: dict[str, Any] = {
            "authorization_model_id": self._settings.model_id,
            "conflict": ConflictOptions(
                on_duplicate_writes=ClientWriteRequestOnDuplicateWrites.IGNORE,
                on_missing_deletes=ClientWriteRequestOnMissingDeletes.IGNORE,
            ),
        }
        try:
            self._client.write(body, options)
        except Exception as exc:
            raise AuthorizationProviderError("OpenFGA relationship write failed") from exc

    def _relationship_present(self, tuple_key: Any) -> bool:
        body = ReadRequestTupleKey(user=tuple_key.user, relation=tuple_key.relation, object=tuple_key.object)
        try:
            response = self._client.read(body, self._options)
        except Exception as exc:
            raise AuthorizationProviderError("OpenFGA relationship read failed") from exc
        return bool(response.tuples)

    def read_relationships(
        self,
        change: AuthorizationRelationshipChange | VersionedRelationshipChange,
    ) -> RelationshipState:
        if isinstance(change, VersionedRelationshipChange):
            complete = all(self._relationship_present(item) for item in change.writes)
            return RelationshipState(
                grant_present=complete and change.effect == PolicyEffect.GRANT,
                deny_present=complete and change.effect == PolicyEffect.REVOKE,
            )
        grant = change.grant_tuple
        deny = change.deny_tuple
        return RelationshipState(
            grant_present=self._relationship_present(grant),
            deny_present=self._relationship_present(deny),
        )

    def read_changes(
        self,
        object_type: RelationshipObjectType,
        *,
        continuation_token: str = "",
        page_size: int = 100,
    ) -> RelationshipChangePage:
        if object_type not in {
            "principal",
            "group",
            "role",
            "installation",
            "account",
            "organization",
            "workspace",
            "event",
            "range",
        }:
            raise AuthorizationProviderError("Invalid OpenFGA diagnostic object type")
        if not 1 <= page_size <= 100:
            raise AuthorizationProviderError("Invalid OpenFGA diagnostic page size")
        options: dict[str, object] = {"page_size": page_size}
        if continuation_token:
            options["continuation_token"] = continuation_token
        try:
            response = self._client.read_changes(ClientReadChangesRequest(type=object_type), options)
        except Exception as exc:
            raise AuthorizationProviderError("OpenFGA change read failed") from exc
        return RelationshipChangePage(
            change_count=len(response.changes or ()),
            continuation_token=response.continuation_token or "",
        )
