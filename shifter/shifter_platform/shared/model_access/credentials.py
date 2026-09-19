"""Secret-bearing control responses with redacted representations."""

from datetime import datetime
from uuid import UUID

from pydantic import SecretStr

from shared.model_access.core_models import AccessLimits, ClosedModel, ModelShard


class ModelEnrollment(ClosedModel):
    """Returned once to the authenticated generation-bound provisioner."""

    grant_id: UUID
    enrollment_token: SecretStr
    expires_at: datetime


class ModelTokenPair(ClosedModel):
    """Returned once by exchange or serialized refresh; never persisted raw."""

    access_token: SecretStr
    refresh_token: SecretStr
    access_expires_at: datetime
    hard_expires_at: datetime


class ModelAccessAuthorization(ClosedModel):
    """Current Engine authority returned to the broker, without provider secrets."""

    allocation_id: UUID
    operation_id: UUID
    grant_epoch: int
    aliases: dict[str, ModelShard]
    limits: AccessLimits
    hard_expires_at: datetime
