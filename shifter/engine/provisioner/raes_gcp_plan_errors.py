"""Errors raised while projecting portable RAES intent into a GCE plan."""


class RaesGcePlanError(RuntimeError):
    """Raised when an RAES plan cannot be realized as a GCE range-cell plan."""
