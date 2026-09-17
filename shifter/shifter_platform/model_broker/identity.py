"""Broker workload authentication independent of provider invocation credentials."""

import asyncio

from shared.model_access import ContractError
from shared.model_access.control_identity import aws_control_assertion


class WorkloadIdentity:
    """Use the broker's own cloud identity for the fixed Engine audience."""

    def __init__(self, *, provider, region, audience):
        if provider not in {"aws", "gcp"} or not audience or len(audience) > 256:
            raise ValueError("invalid broker control identity configuration")
        self.provider, self.region, self.audience = provider, region, audience

    async def __call__(self):
        try:
            return await asyncio.to_thread(self._assertion)
        except Exception:
            raise ContractError("control.identity_unavailable") from None

    def _assertion(self):
        if self.provider == "gcp":
            import requests
            from google.auth.compute_engine import IDTokenCredentials
            from google.auth.transport.requests import Request

            with requests.Session() as session:
                session.trust_env = False
                request = Request(session=session)

                def bounded(*args, **kwargs):
                    kwargs["timeout"] = 2
                    return request(*args, **kwargs)

                credential = IDTokenCredentials(
                    request=bounded, target_audience=self.audience, use_metadata_identity_endpoint=True
                )
                credential.refresh(bounded)
                return "Bearer " + credential.token
        import boto3

        credential = boto3.Session(region_name=self.region).get_credentials()
        if credential is None:
            raise ValueError("broker workload credentials unavailable")
        return aws_control_assertion(
            credentials=credential.get_frozen_credentials(), region=self.region, audience=self.audience
        )
