"""Bounded HTTPS worker protocol with a Secret-injected, attempt-scoped credential."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

import requests
from shared.preparation_grant import PreparationGrantConfiguration
from shared.raes.json_ingress import parse_bounded_json_object

from preparation.compute import ComputeClient
from preparation.worker import execute_attempt

_LIMIT = 262144
_PATH = "/api/v1/cms/artifact-preparation/workers/"


class HTTPSession(Protocol):
    """Minimal requests-compatible session used by the bounded worker client."""

    trust_env: bool

    def request(self, method: str, url: str, **kwargs: object) -> requests.Response: ...


class WorkerClient:
    """One tenant endpoint; no redirects, ambient proxies or unbounded response bodies."""

    def __init__(
        self,
        endpoint: str,
        operation_id: UUID,
        token: str,
        session: HTTPSession | None = None,
    ) -> None:
        url = urlsplit(endpoint)
        try:
            port = url.port
        except ValueError as exc:
            raise ValueError("invalid preparation worker endpoint") from exc
        if (
            url.scheme != "https"
            or not url.hostname
            or port not in {None, 443}
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path != _PATH
        ):
            raise ValueError("invalid preparation worker endpoint")
        if not token or len(token) > 256 or any(char.isspace() for char in token):
            raise ValueError("invalid preparation worker credential")
        self.url = endpoint + str(operation_id) + "/"
        self._token = token
        self.session = session or requests.Session()
        self.session.trust_env = False

    def read(self) -> dict[str, Any]:
        """Fetch only the authenticated immutable attempt envelope."""
        return self._request("GET", expected_status=200)

    def submit(self, result: dict[str, Any]) -> None:
        """Acknowledge durable receipt only; domain admission belongs to the controller."""
        if self._request("POST", expected_status=202, json=result) != {"status": "received"}:
            raise ValueError("preparation result receipt was not acknowledged")

    def _request(self, method: str, *, expected_status: int, **kwargs: Any) -> dict[str, Any]:
        response = self.session.request(
            method,
            self.url,
            headers={"Authorization": "Bearer " + self._token},
            allow_redirects=False,
            timeout=30,
            stream=True,
            **kwargs,
        )
        try:
            if response.status_code != expected_status:
                raise ValueError("preparation worker request was rejected")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                body.extend(chunk)
                if len(body) > _LIMIT:
                    raise ValueError("preparation worker response exceeds its bound")
            return parse_bounded_json_object(bytes(body), max_bytes=_LIMIT)
        finally:
            response.close()


def main() -> None:
    """Run only the operation/attempt selected by workload admission."""
    operation_id = UUID(os.environ["PREPARATION_OPERATION_ID"])
    attempt_id = UUID(os.environ["PREPARATION_ATTEMPT_ID"])
    token = os.environ.pop("PREPARATION_TOKEN")
    client = WorkerClient(os.environ["PREPARATION_ENDPOINT"], operation_id, token)
    envelope = client.read()
    received_attempt_id = UUID(str(envelope.get("attempt_id", "")))
    input_payload = envelope.get("input")
    if not isinstance(input_payload, dict):
        raise ValueError("preparation input does not match the dispatched attempt")
    received_operation_id = UUID(str(input_payload.get("operation_id", "")))
    if (received_attempt_id, received_operation_id) != (attempt_id, operation_id):
        raise ValueError("preparation input does not match the dispatched attempt")

    def cloud(grant: PreparationGrantConfiguration) -> ComputeClient:
        """Handle cloud."""
        return ComputeClient(grant.project_id, grant.zone, grant.max_duration_seconds)

    result = execute_attempt(envelope, Path(__file__).parent, cloud)
    client.submit(result)
    print("preparation receipt recorded", flush=True)  # noqa: T201 - fixed bounded worker progress


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("preparation worker failed") from None
