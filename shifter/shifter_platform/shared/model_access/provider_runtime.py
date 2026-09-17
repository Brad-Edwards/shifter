"""Deployment-owned provider targets; participants select only logical aliases."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from shared.model_access import ContractError
from shared.model_access.core_models import ClosedModel, Identifier, ModelShard


class ProviderTarget(ClosedModel):
    """Explicit approved transport, principal and conservative model context bound."""

    shard_id: Identifier
    provider: Literal["vertex-v1", "bedrock-v1"]
    region: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")]
    model: Annotated[str, Field(pattern=r"^[a-zA-Z0-9@.:/-]{1,256}$")]
    credential_reference: Annotated[str, Field(max_length=256)]
    principal: Annotated[str, Field(max_length=256)]
    project: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$|^$")] = ""
    # Counts may have a different service geography; it must be explicitly
    # approved in deployment intent, never inferred from the compute region.
    count_region: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$|^$")] = ""
    context_window_tokens: Annotated[int, Field(strict=True, ge=1, le=2_000_000)]

    @model_validator(mode="after")
    def validate_identity(self):
        import re

        if self.provider == "vertex-v1":
            if not self.project or self.count_region not in {"us", "eu", "asia-southeast1"}:
                raise ValueError("Vertex requires approved project and count geography")
            if not re.fullmatch(
                r"[a-z][a-z0-9-]{4,28}[a-z0-9]@" + re.escape(self.project) + r"\.iam\.gserviceaccount\.com",
                self.principal,
            ):
                raise ValueError("Vertex principal must belong to the approved project")
            if not re.fullmatch(r"publishers/anthropic/models/[a-z0-9@-]+", self.model):
                raise ValueError("Vertex requires a pinned Anthropic publisher model")
        elif (
            self.project
            or self.count_region
            or not re.fullmatch(r"arn:aws:iam::[0-9]{12}:role/[a-zA-Z0-9/+=,.@_-]+", self.principal)
        ):
            raise ValueError("Bedrock requires an approved invocation role")
        return self

    def bind(self, shard: ModelShard):
        if (
            shard.shard_id != self.shard_id
            or shard.provider_adapter_id != self.provider
            or shard.region != self.region
            or shard.provider_model != self.model
            or shard.credential_ref.reference != self.credential_reference
            or shard.protocol != "anthropic-messages/2023-06-01"
        ):
            raise ContractError("provider.target_mismatch")
        return self


class ProviderInventory(ClosedModel):
    """Closed mounted non-secret configuration independent of guest packages."""

    contract_version: Literal["model-broker-providers/v1"]
    targets: Annotated[list[ProviderTarget], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def unique_targets(self):
        if len({target.shard_id for target in self.targets}) != len(self.targets):
            raise ValueError("duplicate provider target")
        return self
