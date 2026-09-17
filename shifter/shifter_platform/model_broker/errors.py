"""Closed provider outcomes; no upstream payloads or credentials in exceptions."""

from shared.model_access import ContractError
from shared.model_access.provider import ProviderUsage, VerifiedUsage


class NoBillableEffect(ContractError):
    """The adapter proves it never started a billable invocation."""

    def __init__(self, code: str, *, count_only: bool):
        super().__init__(code)
        components = ("request",) if count_only else ("input_tokens", "output_tokens")
        self.usage = ProviderUsage(
            items=tuple(VerifiedUsage(component=component, units=0, provider_verified=True) for component in components)
        )
