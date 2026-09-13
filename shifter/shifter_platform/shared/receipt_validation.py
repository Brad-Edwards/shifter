"""Trusted contracts for participant-bound signed-receipt validation.

The objects in this module are dependency-neutral values shared by CTF, CMS,
and Engine.  They deliberately separate the registration demand (which may
carry a secret *reference*) from the safe binding projection returned to the
scoring path (which never does).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SECRET_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,499}$")
_MAX_OBJECTIVES = 64
_MAX_PUBLIC_KEY_LENGTH = 8192


class ReceiptContractError(ValueError):
    """A signed-receipt contract value is malformed or internally inconsistent."""


class ReceiptKeyMode(StrEnum):
    """Closed verification-key modes supported by the registration contract."""

    REMOTE_SYMMETRIC = "remote_symmetric"
    ASYMMETRIC_PUBLIC = "asymmetric_public"


@dataclass(frozen=True, slots=True)
class ReceiptRegistrationDemand:
    """Trusted request to bind one provider verifier to one range assignment."""

    deployment_id: str
    profile_id: str
    provider_contract: str
    ctf_event_id: UUID
    ctf_participant_id: UUID
    objectives: tuple[str, ...]
    issuer_id: str
    provider_range_namespace: str
    provider_participant_namespace: str
    key_mode: ReceiptKeyMode
    algorithm_id: str
    key_id: str
    reset_generation: int
    secret_version_ref: str = ""
    public_verification_key: str = ""

    def __post_init__(self) -> None:
        """Reject ambiguous, mutable, or secret-shape-invalid demands."""
        for field_name in ("deployment_id", "profile_id", "provider_contract", "issuer_id"):
            _validate_identifier(field_name, getattr(self, field_name))
        _validate_uuid("ctf_event_id", self.ctf_event_id)
        _validate_uuid("ctf_participant_id", self.ctf_participant_id)
        _validate_objectives(self.objectives)
        _validate_identifier("provider_range_namespace", self.provider_range_namespace)
        _validate_identifier("provider_participant_namespace", self.provider_participant_namespace)
        if not isinstance(self.key_mode, ReceiptKeyMode):
            raise ReceiptContractError("key_mode must be a ReceiptKeyMode")
        _validate_identifier("algorithm_id", self.algorithm_id)
        _validate_identifier("key_id", self.key_id)
        if isinstance(self.reset_generation, bool) or not isinstance(self.reset_generation, int):
            raise ReceiptContractError("reset_generation must be a non-negative integer")
        if self.reset_generation < 0:
            raise ReceiptContractError("reset_generation must be a non-negative integer")
        _validate_key_material(self)


@dataclass(frozen=True, slots=True)
class ReceiptVerifierBinding:
    """Safe, immutable verifier projection exposed to downstream scoring code."""

    registration_revision: UUID
    materialization_id: UUID
    assignment_epoch: UUID
    deployment_id: str
    profile_id: str
    provider_contract: str
    ctf_event_id: UUID
    ctf_participant_id: UUID
    objectives: tuple[str, ...]
    issuer_id: str
    key_mode: ReceiptKeyMode
    algorithm_id: str
    key_id: str
    public_verification_key: str
    provider_range_namespace: str
    provider_participant_namespace: str
    reset_generation: int

    def __post_init__(self) -> None:
        """Reject a malformed or secret-bearing downstream projection."""
        for field_name in ("registration_revision", "materialization_id", "assignment_epoch"):
            _validate_uuid(field_name, getattr(self, field_name))
        for field_name in ("deployment_id", "profile_id", "provider_contract", "issuer_id"):
            _validate_identifier(field_name, getattr(self, field_name))
        _validate_uuid("ctf_event_id", self.ctf_event_id)
        _validate_uuid("ctf_participant_id", self.ctf_participant_id)
        _validate_objectives(self.objectives)
        _validate_identifier("provider_range_namespace", self.provider_range_namespace)
        _validate_identifier("provider_participant_namespace", self.provider_participant_namespace)
        if not isinstance(self.key_mode, ReceiptKeyMode):
            raise ReceiptContractError("key_mode must be a ReceiptKeyMode")
        _validate_identifier("algorithm_id", self.algorithm_id)
        _validate_identifier("key_id", self.key_id)
        if (
            not isinstance(self.public_verification_key, str)
            or len(self.public_verification_key) > _MAX_PUBLIC_KEY_LENGTH
            or "\x00" in self.public_verification_key
        ):
            raise ReceiptContractError("public_verification_key must be bounded public material")
        if self.key_mode is ReceiptKeyMode.REMOTE_SYMMETRIC and self.public_verification_key:
            raise ReceiptContractError("remote_symmetric binding cannot expose verification material")
        if self.key_mode is ReceiptKeyMode.ASYMMETRIC_PUBLIC and not self.public_verification_key:
            raise ReceiptContractError("asymmetric_public binding requires verification material")
        if isinstance(self.reset_generation, bool) or not isinstance(self.reset_generation, int):
            raise ReceiptContractError("reset_generation must be a non-negative integer")
        if self.reset_generation < 0:
            raise ReceiptContractError("reset_generation must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class ReceiptSubmissionContext:
    """Server-derived CTF facts available before a verifier profile is selected."""

    event_id: UUID
    participant_id: UUID
    challenge_id: UUID
    range_instance_id: int | None
    owner_user_id: int

    def __post_init__(self) -> None:
        for field_name in ("event_id", "participant_id", "challenge_id"):
            _validate_uuid(field_name, getattr(self, field_name))
        if self.range_instance_id is not None and (
            isinstance(self.range_instance_id, bool)
            or not isinstance(self.range_instance_id, int)
            or self.range_instance_id <= 0
        ):
            raise ReceiptContractError("range_instance_id must be a positive integer or None")
        if isinstance(self.owner_user_id, bool) or not isinstance(self.owner_user_id, int) or self.owner_user_id <= 0:
            raise ReceiptContractError("owner_user_id must be a positive integer")


@dataclass(frozen=True, slots=True)
class ReceiptValidationContext:
    """Exact trusted binding passed to a receipt-capable verifier."""

    deployment_id: str
    profile_id: str
    provider_contract: str
    event_id: UUID
    participant_id: UUID
    challenge_id: UUID
    range_instance_id: int
    materialization_id: UUID
    assignment_epoch: UUID
    registration_revision: UUID
    provider_range_namespace: str
    provider_participant_namespace: str
    issuer_id: str
    key_mode: ReceiptKeyMode
    algorithm_id: str
    key_id: str
    public_verification_key: str
    reset_generation: int
    registered_objectives: tuple[str, ...]
    objective_id: str

    def __post_init__(self) -> None:
        for field_name in ("deployment_id", "profile_id", "provider_contract", "issuer_id", "objective_id"):
            _validate_identifier(field_name, getattr(self, field_name))
        for field_name in (
            "event_id",
            "participant_id",
            "challenge_id",
            "materialization_id",
            "assignment_epoch",
            "registration_revision",
        ):
            _validate_uuid(field_name, getattr(self, field_name))
        if isinstance(self.range_instance_id, bool) or not isinstance(self.range_instance_id, int):
            raise ReceiptContractError("range_instance_id must be a positive integer")
        if self.range_instance_id <= 0:
            raise ReceiptContractError("range_instance_id must be a positive integer")
        _validate_identifier("provider_range_namespace", self.provider_range_namespace)
        _validate_identifier("provider_participant_namespace", self.provider_participant_namespace)
        if not isinstance(self.key_mode, ReceiptKeyMode):
            raise ReceiptContractError("key_mode must be a ReceiptKeyMode")
        _validate_identifier("algorithm_id", self.algorithm_id)
        _validate_identifier("key_id", self.key_id)
        if (
            not isinstance(self.public_verification_key, str)
            or len(self.public_verification_key) > _MAX_PUBLIC_KEY_LENGTH
            or "\x00" in self.public_verification_key
        ):
            raise ReceiptContractError("public_verification_key must be bounded public material")
        if self.key_mode is ReceiptKeyMode.REMOTE_SYMMETRIC and self.public_verification_key:
            raise ReceiptContractError("remote_symmetric context cannot expose verification material")
        if self.key_mode is ReceiptKeyMode.ASYMMETRIC_PUBLIC and not self.public_verification_key:
            raise ReceiptContractError("asymmetric_public context requires verification material")
        if isinstance(self.reset_generation, bool) or not isinstance(self.reset_generation, int):
            raise ReceiptContractError("reset_generation must be a non-negative integer")
        if self.reset_generation < 0:
            raise ReceiptContractError("reset_generation must be a non-negative integer")
        _validate_objectives(self.registered_objectives)
        if self.objective_id not in self.registered_objectives:
            raise ReceiptContractError("objective_id must belong to the registered objectives")


@dataclass(frozen=True, slots=True)
class VerifiedReceiptEvidence:
    """Non-consuming verifier result carried into the scoring transaction."""

    receipt_id: str
    issuer_id: str
    expires_at: datetime
    context: ReceiptValidationContext

    def __post_init__(self) -> None:
        if (
            not isinstance(self.receipt_id, str)
            or not 1 <= len(self.receipt_id) <= 256
            or self.receipt_id != self.receipt_id.strip()
            or any(character in self.receipt_id for character in ("\x00", "\r", "\n"))
        ):
            raise ReceiptContractError("receipt_id must be a bounded opaque identifier")
        _validate_identifier("issuer_id", self.issuer_id)
        if not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None:
            raise ReceiptContractError("expires_at must be timezone-aware")
        if not isinstance(self.context, ReceiptValidationContext):
            raise ReceiptContractError("context must be a ReceiptValidationContext")
        if self.issuer_id != self.context.issuer_id:
            raise ReceiptContractError("receipt issuer does not match the registered context")


def _validate_identifier(field_name: str, value: object) -> None:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ReceiptContractError(f"{field_name} is not a canonical identifier")


def _validate_uuid(field_name: str, value: object) -> None:
    if not isinstance(value, UUID):
        raise ReceiptContractError(f"{field_name} must be a UUID")


def _validate_objectives(objectives: object) -> None:
    if not isinstance(objectives, tuple) or not 1 <= len(objectives) <= _MAX_OBJECTIVES:
        raise ReceiptContractError(f"objectives must be a tuple containing 1-{_MAX_OBJECTIVES} identifiers")
    if len(set(objectives)) != len(objectives):
        raise ReceiptContractError("objectives must not contain duplicates")
    for objective in objectives:
        _validate_identifier("objective", objective)


def _validate_key_material(demand: ReceiptRegistrationDemand) -> None:
    secret_ref = demand.secret_version_ref
    public_key = demand.public_verification_key
    if not isinstance(secret_ref, str) or not isinstance(public_key, str):
        raise ReceiptContractError("verification key fields must be strings")
    if demand.key_mode is ReceiptKeyMode.REMOTE_SYMMETRIC:
        if _SECRET_REF_RE.fullmatch(secret_ref) is None or public_key:
            raise ReceiptContractError("remote_symmetric requires only a canonical secret version reference")
        return
    if secret_ref or not public_key or len(public_key) > _MAX_PUBLIC_KEY_LENGTH or "\x00" in public_key:
        raise ReceiptContractError("asymmetric_public requires only a bounded public verification key")
