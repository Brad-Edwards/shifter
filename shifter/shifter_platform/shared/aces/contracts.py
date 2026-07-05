"""Runtime-safe ACES profile and operation sidecar constants.

The manifest builder imports the ACES SDL tooling; normal Django runtime paths
must not. Keep constants that model validators need here so sidecar persistence
can check Shifter's supported profile/version set without importing the builder.
"""

from __future__ import annotations

SHIFTER_BACKEND_NAME = "shifter"
SHIFTER_BACKEND_PROFILE = "provisioning-only"

SHIFTER_SUPPORTED_CONTRACT_VERSIONS = frozenset(
    {
        "backend-manifest-v2",
        "operation-receipt-v1",
        "operation-status-v1",
        "runtime-snapshot-v1",
    }
)

SHIFTER_SUPPORTED_SIDECAR_REFERENCE_VERSIONS = frozenset({"execution-plan-ref-v1"})

SHIFTER_SUPPORTED_OPERATION_RECORD_VERSIONS = (
    SHIFTER_SUPPORTED_CONTRACT_VERSIONS | SHIFTER_SUPPORTED_SIDECAR_REFERENCE_VERSIONS
) - {"backend-manifest-v2"}

OPERATION_RECORD_KIND_TO_CONTRACT_VERSIONS = {
    "operation_receipt": frozenset({"operation-receipt-v1"}),
    "operation_status": frozenset({"operation-status-v1"}),
    "runtime_snapshot": frozenset({"runtime-snapshot-v1"}),
    "execution_plan_ref": frozenset({"execution-plan-ref-v1"}),
}
