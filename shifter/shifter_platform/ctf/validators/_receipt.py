"""Authenticated, DNS-pinned transport for receipt-v1 validation."""

from __future__ import annotations

import contextlib
import http.client
import json
import logging
import ssl
import time
from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone

from shared.cloud import get_secrets_store
from shared.receipt_validation import ReceiptValidationContext, VerifiedReceiptEvidence

from ._receipt_profiles import ReceiptVerifierProfile
from ._ssrf import _safe_parse_url

logger = logging.getLogger(__name__)

_RECEIPT_PROTOCOL = "shifter-receipt-v1"
_MAX_RECEIPT_BYTES = 4096
_INVALID_RESPONSE = object()
_REJECTED_RECEIPT = object()
_RESPONSE_KEYS = frozenset({"valid", "receipt_id", "issuer", "expires_at", "binding"})
_BINDING_KEYS = frozenset(
    {
        "event",
        "participant",
        "challenge",
        "range_instance",
        "materialization",
        "assignment_epoch",
        "registration_revision",
        "reset_generation",
        "objective",
    }
)


def validate_receipt(
    submitted_receipt: str,
    profile: ReceiptVerifierProfile,
    context: ReceiptValidationContext,
) -> VerifiedReceiptEvidence | None:
    """Verify a receipt through one exact authenticated deployment profile."""
    started = time.monotonic()
    if not _request_inputs_are_valid(submitted_receipt, profile, context):
        return None
    parsed_tuple = _safe_parse_url(profile.endpoint_url)
    if parsed_tuple is None:
        return None
    parsed, hostname, port = parsed_tuple
    try:
        service_auth = get_secrets_store().get_secret(profile.service_auth_secret_ref)
    except Exception:
        logger.warning("Receipt validator service authentication is unavailable")
        return None
    if not isinstance(service_auth, str) or not service_auth or len(service_auth) > 8192:
        return None
    if _remaining(profile, started) <= 0:
        return None

    payload = {
        "contract": _RECEIPT_PROTOCOL,
        "provider_contract": profile.provider_contract,
        "audience": profile.audience,
        "receipt": submitted_receipt,
        "binding": _binding_payload(context),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(body) > profile.max_request_bytes:
        return None
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "X-Service-Token": service_auth,
    }

    from ctf import validators as public_validators

    pinned_ips = public_validators._resolve_target(hostname, port, context.challenge_id)
    if not pinned_ips or _remaining(profile, started) <= 0:
        return None
    request_path = parsed.path or "/"
    for pinned_ip in pinned_ips[: profile.max_addresses]:
        response = _request_one_address(
            hostname=hostname,
            pinned_ip=pinned_ip,
            port=port,
            timeout=_remaining(profile, started),
            request_path=request_path,
            body=body,
            headers=headers,
            max_response_bytes=profile.max_response_bytes,
        )
        if response is not None:
            parsed_evidence = _parse_evidence(response, profile, context)
            if parsed_evidence is _INVALID_RESPONSE:
                logger.warning("Receipt validator returned an invalid response")
                return None
            if parsed_evidence is _REJECTED_RECEIPT:
                return None
            if isinstance(parsed_evidence, VerifiedReceiptEvidence):
                return parsed_evidence
            logger.warning("Receipt validator returned an invalid response")
            return None
        if _remaining(profile, started) <= 0:
            break
    logger.warning("Receipt validator provider is unavailable")
    return None


def _request_inputs_are_valid(
    submitted_receipt: object,
    profile: object,
    context: object,
) -> bool:
    if (
        not isinstance(submitted_receipt, str)
        or not submitted_receipt
        or submitted_receipt != submitted_receipt.strip()
        or any(character in submitted_receipt for character in ("\x00", "\r", "\n"))
        or len(submitted_receipt.encode("utf-8")) > _MAX_RECEIPT_BYTES
        or not isinstance(profile, ReceiptVerifierProfile)
        or not isinstance(context, ReceiptValidationContext)
    ):
        return False
    return bool(
        context.profile_id == profile.profile_id
        and context.deployment_id == profile.deployment_id
        and context.provider_contract == profile.provider_contract
        and context.issuer_id == profile.issuer_id
        and context.key_mode in profile.allowed_key_modes
        and context.algorithm_id in profile.allowed_algorithms
        and context.objective_id in profile.permitted_objectives
    )


def _binding_payload(context: ReceiptValidationContext) -> dict[str, object]:
    return {
        "event": str(context.event_id),
        "participant": context.provider_participant_namespace,
        "challenge": str(context.challenge_id),
        "range_instance": context.provider_range_namespace,
        "materialization": str(context.materialization_id),
        "assignment_epoch": str(context.assignment_epoch),
        "registration_revision": str(context.registration_revision),
        "reset_generation": context.reset_generation,
        "objective": context.objective_id,
    }


def _remaining(profile: ReceiptVerifierProfile, started: float) -> float:
    return max(0.0, profile.total_timeout_seconds - (time.monotonic() - started))


def _request_one_address(
    *,
    hostname: str,
    pinned_ip: str,
    port: int,
    timeout: float,
    request_path: str,
    body: bytes,
    headers: dict[str, str],
    max_response_bytes: int,
) -> bytes | None:
    """Attempt one bounded pinned connection and return one complete body."""
    if timeout <= 0:
        return None
    conn: http.client.HTTPSConnection | None = None
    try:
        tls_context = ssl.create_default_context()
        tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
        from ctf import validators as public_validators

        conn = public_validators._build_https_connection(
            hostname=hostname,
            pinned_ip=pinned_ip,
            port=port,
            timeout=timeout,
            context=tls_context,
        )
        conn.request("POST", request_path, body=body, headers=headers)
        response = conn.getresponse()
        if response.status != 200:
            return b""
        raw = response.read(max_response_bytes + 1)
        return raw if len(raw) <= max_response_bytes else b""
    except (TimeoutError, ssl.SSLError, OSError, http.client.HTTPException):
        return None
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()


def _parse_evidence(
    raw: bytes,
    profile: ReceiptVerifierProfile,
    context: ReceiptValidationContext,
) -> VerifiedReceiptEvidence | object:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (TypeError, ValueError, UnicodeDecodeError):
        return _INVALID_RESPONSE
    if value == {"valid": False}:
        return _REJECTED_RECEIPT
    if not isinstance(value, dict) or set(value) != _RESPONSE_KEYS or value.get("valid") is not True:
        return _INVALID_RESPONSE
    if value.get("issuer") != profile.issuer_id or value.get("binding") != _binding_payload(context):
        return _INVALID_RESPONSE
    receipt_id = value.get("receipt_id")
    issuer_id = value.get("issuer")
    if not isinstance(receipt_id, str) or not isinstance(issuer_id, str):
        return _INVALID_RESPONSE
    expires_at = _parse_expiry(value.get("expires_at"), profile.max_receipt_ttl_seconds)
    if expires_at is None:
        return _INVALID_RESPONSE
    try:
        return VerifiedReceiptEvidence(
            receipt_id=receipt_id,
            issuer_id=issuer_id,
            expires_at=expires_at,
            context=context,
        )
    except (TypeError, ValueError):
        return _INVALID_RESPONSE


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _parse_expiry(value: object, max_ttl_seconds: int) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = timezone.now()
    if expires_at.tzinfo is None or not now < expires_at <= now + timedelta(seconds=max_ttl_seconds):
        return None
    return expires_at
