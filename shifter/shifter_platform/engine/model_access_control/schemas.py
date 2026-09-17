"""Closed control requests; no prompts or upstream URLs enter Engine."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, SecretStr

from shared.model_access import BillingBound
from shared.model_access.core_models import ClosedModel, Identifier
from shared.model_access.provider import ProviderUsage


class TokenRequest(ClosedModel):
    token: SecretStr
    transport_peer: Annotated[str, Field(max_length=45)]


class EnrollmentRequest(ClosedModel):
    allocation_id: UUID
    operation_id: UUID


class ReservationRequest(TokenRequest):
    request_uuid: UUID
    logical_alias: Identifier
    billing_bound: BillingBound
    caller_key_hmac: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    intent_fingerprint_hmac: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    key_version: Identifier | None = None
    prior_caller_key_hmacs: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], ...], Field(max_length=8)
    ] = ()
    prior_intent_fingerprint_hmacs: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")], ...], Field(max_length=8)
    ] = ()
    retained_key_versions: Annotated[tuple[Identifier, ...], Field(max_length=8)] = ()


class AdvanceRequest(TokenRequest):
    request_uuid: UUID
    action: Literal["dispatch", "check", "continue"]
    dispatch_token: SecretStr = SecretStr("")


class FinishRequest(ClosedModel):
    request_uuid: UUID
    action: Literal["settle", "unknown", "release"]
    usage: ProviderUsage | None = None
