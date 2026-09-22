"""Google-native service ID-token verification at the composition boundary."""

from functools import partial

from django.conf import settings
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from management.services import resolve_service_credential
from shared.credentials import CredentialContext


def verify_service_credential(raw_token: str) -> CredentialContext:
    """Require exact Google proof, configured audience and explicit SQL admission."""
    audience = getattr(settings, "GCP_SERVICE_TOKEN_AUDIENCE", "")
    if not audience or not audience.startswith("https://"):
        raise ValueError("Service authentication unavailable")
    claims = id_token.verify_oauth2_token(raw_token, partial(Request(), timeout=10), audience=audience)
    subject = claims.get("sub")
    email = claims.get("email")
    if (
        claims.get("iss") != "https://accounts.google.com"
        or claims.get("aud") != audience
        or claims.get("email_verified") is not True
        or not isinstance(subject, str)
        or not subject.isascii()
        or not subject.isdecimal()
        or not 10 <= len(subject) <= 32
        or not isinstance(email, str)
        or not email.endswith(".gserviceaccount.com")
    ):
        raise ValueError("Service authentication denied")
    return resolve_service_credential(issuer=claims["iss"], subject=subject, audience=audience)
