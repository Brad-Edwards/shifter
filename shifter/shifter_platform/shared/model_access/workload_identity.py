"""Bounded workload assertions shared by broker and trusted guest provisioner."""

from shared.model_access import ContractError


def workload_assertion(*, provider: str, region: str, audience: str) -> str:
    try:
        if not audience or len(audience) > 256:
            raise ValueError
        if provider == "gcp":
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
                    request=bounded, target_audience=audience, use_metadata_identity_endpoint=True
                )
                credential.refresh(bounded)
                return "Bearer " + credential.token
        if provider != "aws":
            raise ValueError
        import boto3

        from shared.model_access.control_identity import aws_control_assertion

        credential = boto3.Session(region_name=region).get_credentials()
        if credential is None:
            raise ValueError
        return aws_control_assertion(credentials=credential.get_frozen_credentials(), region=region, audience=audience)
    except Exception:
        raise ContractError("control.identity_unavailable") from None
