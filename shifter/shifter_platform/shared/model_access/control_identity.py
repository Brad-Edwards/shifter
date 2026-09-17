"""Workload identity for private broker control; no guest credential fallback."""

from __future__ import annotations

import base64
import json
import re
from datetime import UTC, datetime

from shared.model_access import ContractError
from shared.model_access.messages import strict_json

_STS_BODY = "Action=GetCallerIdentity&Version=2011-06-15"
_AUDIENCE_HEADER = "x-shifter-audience"


def aws_control_assertion(*, credentials, region: str, audience: str) -> str:
    """Sign a fixed regional STS identity request and bind the control audience."""
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    origin = _sts_origin(region)
    request = AWSRequest(
        method="POST",
        url=origin,
        data=_STS_BODY,
        headers={
            "Host": origin.removeprefix("https://"),
            "Content-Type": "application/x-www-form-urlencoded",
            _AUDIENCE_HEADER: audience,
        },
    )
    SigV4Auth(credentials, "sts", region).add_auth(request)
    body = json.dumps({"headers": {key.lower(): value for key, value in request.headers.items()}}).encode()
    return "AWS-STS " + base64.urlsafe_b64encode(body).decode("ascii")


def _sts_origin(region: str) -> str:
    if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", region):
        raise ContractError("control.invalid_region")
    return f"https://sts.{region}.amazonaws.com"


def verify_aws_control_assertion(
    assertion: str,
    *,
    region: str,
    audience: str,
    expected_role: str,
    session,
    now: datetime | None = None,
) -> None:
    """Replay only a fixed, bounded signed STS call; never a caller URL or body."""
    from defusedxml import ElementTree

    try:
        if not assertion.startswith("AWS-STS ") or len(assertion) > 12_000:
            raise ValueError
        value = strict_json(base64.b64decode(assertion[8:], altchars=b"-_", validate=True), limit=8192)
        headers = value["headers"]
        allowed = {"host", "content-type", "authorization", "x-amz-date", "x-amz-security-token", _AUDIENCE_HEADER}
        if set(value) != {"headers"} or not isinstance(headers, dict) or set(headers) - allowed:
            raise ValueError
        if any(not isinstance(item, str) or "\r" in item or "\n" in item for item in headers.values()):
            raise ValueError
        origin = _sts_origin(region)
        if headers.get("host") != origin.removeprefix("https://") or headers.get(_AUDIENCE_HEADER) != audience:
            raise ValueError
        if headers.get("content-type") != "application/x-www-form-urlencoded":
            raise ValueError
        signed = re.fullmatch(
            r"AWS4-HMAC-SHA256 Credential=[A-Z0-9]+/(\d{8})/([a-z0-9-]+)/sts/aws4_request, "
            r"SignedHeaders=([a-z0-9;-]+), Signature=[0-9a-f]{64}",
            headers["authorization"],
        )
        required = set(headers) - {"authorization"}
        if signed is None or signed.group(2) != region or set(signed.group(3).split(";")) != required:
            raise ValueError
        issued = datetime.strptime(headers["x-amz-date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        if abs(((now or datetime.now(UTC)) - issued).total_seconds()) > 60:
            raise ValueError
        with session.post(
            origin, data=_STS_BODY, headers=headers, timeout=2, allow_redirects=False, stream=True
        ) as response:
            if response.status_code != 200:
                raise ValueError
            raw = response.raw.read(16_385, decode_content=False)
        if len(raw) > 16_384 or b"<!" in raw:
            raise ValueError
        root = ElementTree.fromstring(raw)
        arn = root.findtext(".//{https://sts.amazonaws.com/doc/2011-06-15/}Arn", default="")
        role = re.fullmatch(
            r"arn:aws:iam::([0-9]{12}):role/(?:[A-Za-z0-9+=,.@_-]+/)*([A-Za-z0-9+=,.@_-]+)", expected_role
        )
        if role is None or not re.fullmatch(
            rf"arn:aws:sts::{role.group(1)}:assumed-role/{re.escape(role.group(2))}/[A-Za-z0-9+=,.@_-]+", arn
        ):
            raise ValueError
    except Exception:
        # Never include a signed assertion, credential, upstream body or URL in
        # an auth exception. The caller exposes this closed code only.
        raise ContractError("control.unauthorized") from None


def verify_google_control_assertion(assertion: str, *, audience: str, expected_subject: str, request) -> None:
    """Verify signature, Google issuer, audience, expiry and exact verified email."""
    from google.oauth2.id_token import verify_oauth2_token

    try:
        if not assertion.startswith("Bearer ") or len(assertion) > 16_384:
            raise ValueError
        claims = verify_oauth2_token(assertion[7:], request, audience=audience)
        if claims.get("email") != expected_subject or claims.get("email_verified") is not True:
            raise ValueError
    except Exception:
        raise ContractError("control.unauthorized") from None
