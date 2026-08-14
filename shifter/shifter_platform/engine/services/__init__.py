"""Engine service interface.

Infrastructure lifecycle for Shifter platform. The implementation is split
across private submodules (``_common``, ``_range``, ``_lifecycle``,
``_terminal``, ``_ngfw``, ``_queries``) and re-exported here so callers
continue to use ``from engine.services import X``.

The re-exports also rebind a few names that tests historically patch at
``engine.services.<name>`` (``transaction``, ``get_rdp_password``,
``get_ssh_key``) so existing ``unittest.mock.patch`` targets still work.
"""

from __future__ import annotations

from django.db import transaction

from engine.secrets import SecretsError, get_rdp_password, get_ssh_key
from engine.ssh import SSHConnection

from ._capacity import (
    EventCapacitySignal,
    latest_capacity_declaration,
    record_capacity_declaration,
)
from ._capacity_admit import (
    admit_range_capacity,
    reconcile_capacity_budgets,
    release_range_capacity,
)
from ._capacity_plan import (
    EventCapacityRequest,
    assess_declared_event_capacity,
    assess_event_capacity,
    release_capacity_reservations,
)
from ._common import EngineError
from ._lifecycle import pause_range, resume_range
from ._ngfw import create_ngfw, destroy_ngfw, start_ngfw, stop_ngfw
from ._operation_apply import apply_pending_operation_results, evaluate_operation_result
from ._queries import get_authoritative_range_status, get_ranges_for_ngfw, get_user_ready_range_instances
from ._raes_evidence import record_raes_operation_status, record_raes_runtime_snapshot
from ._raes_image import (
    RaesImageMappingError,
    RaesImageMappingOptions,
    RaesImageMappingView,
    disable_raes_image_mapping,
    list_backend_artifacts,
    list_raes_image_mappings,
    upsert_raes_image_mapping,
)
from ._raes_range import RaesRangeRef, RangeBindings, create_raes_range
from ._raes_status import project_raes_operation_status
from ._range import (
    cancel_range,
    destroy_range,
    get_instance_ips_by_uuid,
    get_range_status,
)
from ._range_by_request import (
    RangeOwnershipTransferBlocked,
    cancel_range_by_request,
    destroy_range_by_request,
    range_owner_reassignment_available_by_request,
    reassign_range_owner_by_request,
    rebind_range_workspace_by_request,
)
from ._range_escape import GuestProbeError, GuestProbeRequest, RangeMembership, get_range_membership, run_guest_probe
from ._subnet_coordination import (
    read_subnet_reservation,
    release_subnet_reservation,
    reserve_subnet_cidrs,
)
from ._terminal import (
    connect_ngfw_terminal,
    connect_terminal,
    get_owned_instance_request_ref,
    get_rdp_connection_info,
    get_ssh_connection_info,
)
from ._vpn import (
    VpnProfileConflict,
    VpnProfileNotFound,
    VpnProfileUnavailable,
    get_openvpn_profile,
    has_openvpn_profile,
)

__all__ = (
    "EngineError",
    "EventCapacityRequest",
    "EventCapacitySignal",
    "GuestProbeError",
    "GuestProbeRequest",
    "RaesImageMappingError",
    "RaesImageMappingOptions",
    "RaesImageMappingView",
    "RaesRangeRef",
    "RangeBindings",
    "RangeMembership",
    "RangeOwnershipTransferBlocked",
    "SSHConnection",
    "SecretsError",
    "VpnProfileConflict",
    "VpnProfileNotFound",
    "VpnProfileUnavailable",
    "admit_range_capacity",
    "apply_pending_operation_results",
    "assess_declared_event_capacity",
    "assess_event_capacity",
    "cancel_range",
    "cancel_range_by_request",
    "connect_ngfw_terminal",
    "connect_terminal",
    "create_ngfw",
    "create_raes_range",
    "destroy_ngfw",
    "destroy_range",
    "destroy_range_by_request",
    "disable_raes_image_mapping",
    "evaluate_operation_result",
    "get_authoritative_range_status",
    "get_instance_ips_by_uuid",
    "get_openvpn_profile",
    "get_owned_instance_request_ref",
    "get_range_membership",
    "get_range_status",
    "get_ranges_for_ngfw",
    "get_rdp_connection_info",
    "get_rdp_password",
    "get_ssh_connection_info",
    "get_ssh_key",
    "get_user_ready_range_instances",
    "has_openvpn_profile",
    "latest_capacity_declaration",
    "list_backend_artifacts",
    "list_raes_image_mappings",
    "pause_range",
    "project_raes_operation_status",
    "range_owner_reassignment_available_by_request",
    "read_subnet_reservation",
    "reassign_range_owner_by_request",
    "rebind_range_workspace_by_request",
    "reconcile_capacity_budgets",
    "record_capacity_declaration",
    "record_raes_operation_status",
    "record_raes_runtime_snapshot",
    "release_capacity_reservations",
    "release_range_capacity",
    "release_subnet_reservation",
    "reserve_subnet_cidrs",
    "resume_range",
    "run_guest_probe",
    "start_ngfw",
    "stop_ngfw",
    "transaction",
    "upsert_raes_image_mapping",
)
