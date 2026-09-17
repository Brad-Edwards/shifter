"""Provider credentials exist in the broker only, scoped to approved identities."""

import asyncio
from collections.abc import Mapping
from contextlib import closing

from shared.model_access.provider_runtime import ProviderTarget


class ProviderCredentials:
    """Short-lived impersonation/assumption, with no guest or request-selected role."""

    async def headers(self, target: ProviderTarget, *, url: str, body: bytes) -> dict[str, str]:
        return await asyncio.to_thread(self._headers, target, url=url, body=body)

    @staticmethod
    def _headers(target: ProviderTarget, *, url: str, body: bytes) -> dict[str, str]:
        if target.provider == "vertex-v1":
            return _vertex_headers(target)
        return _bedrock_headers(target, url=url, body=body)


def _vertex_headers(target: ProviderTarget) -> dict[str, str]:
    """Impersonate the approved service account through a bounded private session."""
    import requests
    from google.auth import compute_engine, impersonated_credentials
    from google.auth.transport import Response
    from google.auth.transport.requests import Request

    with requests.Session() as session:
        session.trust_env = False
        request = Request(session=session)

        def bounded(
            url: str,
            method: str = "GET",
            body: bytes | None = None,
            headers: Mapping[str, str] | None = None,
            **kwargs: object,
        ) -> Response:
            """Bound every credential refresh HTTP call to two seconds."""
            kwargs["timeout"] = 2
            return request(url, method=method, body=body, headers=headers, **kwargs)

        credentials = impersonated_credentials.Credentials(
            source_credentials=compute_engine.Credentials(),
            target_principal=target.principal,
            target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
            lifetime=300,
        )
        credentials.refresh(bounded)
        return {
            "authorization": f"Bearer {credentials.token}",
            "content-type": "application/json",
            "accept-encoding": "identity",
        }


def _bedrock_headers(target: ProviderTarget, *, url: str, body: bytes) -> dict[str, str]:
    """Sign the exact request with a short-lived session for the approved role."""
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    from shared.model_access.aws_session import bounded_aws_session

    with closing(bounded_aws_session(target.region).client("sts")) as client:
        result = client.assume_role(RoleArn=target.principal, RoleSessionName="model-broker", DurationSeconds=900)
    value = result["Credentials"]
    credentials = Credentials(value["AccessKeyId"], value["SecretAccessKey"], value["SessionToken"])
    request = AWSRequest(
        method="POST",
        url=url,
        data=body,
        headers={"content-type": "application/json", "accept-encoding": "identity"},
    )
    SigV4Auth(credentials, "bedrock", target.region).add_auth(request)
    return dict(request.headers.items())
