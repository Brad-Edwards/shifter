"""Resolve a closed public projection from core-owned realized guest outputs."""

from __future__ import annotations

import base64
import ipaddress
import json

from cryptography.hazmat.primitives.serialization import load_ssh_public_key
from shifter_adapter_sdk.guest import MAX_RUNTIME_VALUES_BYTES
from shifter_adapter_sdk.runtime import GuestAction, RuntimeInput


def resolve_runtime_values(action: GuestAction, request: RuntimeInput, outputs: dict[str, dict]) -> str:
    """Encode only declared fields; management keys and arbitrary outputs are absent."""
    values = {}
    for name, reference in action.runtime_values.items():
        target = request.targets[reference.binding]
        output = outputs[f"{target.node_address}#0"]
        if reference.field == "private_address":
            value = str(ipaddress.ip_address(output["private_ip"]))
        else:
            # Never fall back to output['public_key']: that is the management key.
            value = output.get("participant_ssh_public_key")
            if (
                "ssh" not in output.get("participant_access_channels", [])
                or not isinstance(value, str)
                or not value
                or len(value) > 16_384
                or "\n" in value
                or "\r" in value
            ):
                raise ValueError("Participant public key is unavailable")
            load_ssh_public_key(value.encode("ascii"))
        values[name] = value
    raw = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_RUNTIME_VALUES_BYTES:
        raise ValueError("Guest runtime values exceed their bound")
    return base64.b64encode(raw).decode("ascii")
