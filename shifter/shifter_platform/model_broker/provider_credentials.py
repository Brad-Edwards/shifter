"""Provider credentials exist in the broker only, scoped to approved identities."""

import asyncio
from contextlib import closing


class ProviderCredentials:
    """Short-lived impersonation/assumption, with no guest or request-selected role."""

    async def headers(self, target, *, url, body):
        return await asyncio.to_thread(self._headers, target, url=url, body=body)

    @staticmethod
    def _headers(target, *, url, body):
        if target.provider == "vertex-v1":
            import requests
            from google.auth import compute_engine, impersonated_credentials
            from google.auth.transport.requests import Request

            with requests.Session() as session:
                session.trust_env = False
                request = Request(session=session)

                def bounded(*args, **kwargs):
                    kwargs["timeout"] = 2
                    return request(*args, **kwargs)

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
