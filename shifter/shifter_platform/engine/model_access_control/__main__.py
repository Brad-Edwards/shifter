"""Start the dedicated TLS listener after normal Engine secret hydration."""

import os
from collections.abc import Mapping
from uuid import UUID


def main() -> None:
    """Fail closed on missing catalog or workload-identity configuration."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    import requests
    import uvicorn
    from django.conf import settings
    from django.db import connection
    from google.auth.transport import Response
    from google.auth.transport.requests import Request

    from engine.model_access_control.server import ControlApplication
    from shared.model_access import ContractError
    from shared.model_access.control_identity import verify_aws_control_assertion, verify_google_control_assertion

    if not settings.MODEL_ACCESS_ENABLED or settings.MODEL_ACCESS_CATALOG is None:
        raise RuntimeError("model control requires enabled model access")
    provider = os.environ.get("MODEL_CONTROL_PROVIDER", "gcp")
    audience = os.environ["MODEL_CONTROL_AUDIENCE"]
    broker = os.environ["MODEL_CONTROL_BROKER_SUBJECT"]
    provisioner = os.environ["MODEL_CONTROL_PROVISIONER_SUBJECT"]
    if not broker or not provisioner or broker == provisioner or provider not in {"aws", "gcp"}:
        raise RuntimeError("model control requires distinct workload identities")
    session = requests.Session()
    session.trust_env = False
    google_request = Request(session=session)

    def bounded_google_request(
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        **kwargs: object,
    ) -> Response:
        """Limit certificate retrieval during workload verification to two seconds."""
        kwargs["timeout"] = 2
        return google_request(url, method=method, body=body, headers=headers, **kwargs)

    def verify(assertion: str, *, operation_id: UUID | None) -> None:
        """Verify a distinct broker or operation-bound provisioner identity."""
        expected = provisioner if operation_id else broker
        bound_audience = f"{audience}:enroll:{operation_id}" if operation_id else audience
        if provider == "gcp":
            verify_google_control_assertion(
                assertion, audience=bound_audience, expected_subject=expected, request=bounded_google_request
            )
        else:
            verify_aws_control_assertion(
                assertion,
                audience=bound_audience,
                expected_role=expected,
                region=os.environ["MODEL_CONTROL_REGION"],
                session=session,
            )

    def ready() -> bool:
        """Admit readiness only while the Engine database responds."""
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
        except Exception:
            return False

    try:
        uvicorn.run(
            ControlApplication(verify_identity=verify, ready=ready),
            # Private service binding; TLS, workload authentication and default-deny NetworkPolicy apply.
            host="0.0.0.0",  # noqa: S104 # nosec B104 # NOSONAR(S8392)
            port=8444,
            ssl_certfile=os.environ["MODEL_CONTROL_TLS_CERT"],
            ssl_keyfile=os.environ["MODEL_CONTROL_TLS_KEY"],
            proxy_headers=False,
            access_log=False,
            server_header=False,
            lifespan="off",
            limit_concurrency=128,
            backlog=128,
            timeout_keep_alive=5,
            timeout_graceful_shutdown=130,
        )
    except ContractError:
        raise RuntimeError("model control configuration is invalid") from None
    finally:
        session.close()


if __name__ == "__main__":
    main()
