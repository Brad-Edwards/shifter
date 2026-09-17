"""Deliver admitted model access over the generation's trusted guest channel."""

from __future__ import annotations

import base64
import json
import os
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import UUID

from shared.model_access.credentials import ModelEnrollment
from shared.model_access.guest_binding import ModelGuestBinding
from shared.model_access.messages import strict_json
from shared.model_access.network import broker_egress_destination
from shared.model_access.workload_identity import workload_assertion

from executors.factory import build_guest_execution_context

_ORIGIN = re.compile(r"https://[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?(?::[1-9][0-9]{0,4})?")


class ModelEnrollmentError(RuntimeError):
    """Only a closed diagnostic may leave this credential-bearing boundary."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Enrollment redirect rejected")


@dataclass(frozen=True)
class EnrollmentDelivery:
    operation_id: UUID
    bindings: tuple[ModelGuestBinding, ...]
    provider: str
    region: str
    control_url: str
    audience: str
    broker_url: str
    ca_pem: str
    broker_vip: str = ""

    def gce_egress_capability(self) -> dict[str, object]:
        """Only an admitted enrollment opens the exact applied destination."""
        if self.provider != "gcp":
            raise ModelEnrollmentError("GCE broker egress requires the GCP provider")
        capability = {"contract_version": "model-broker-egress/v1", "vip": self.broker_vip, "port": 443}
        broker_egress_destination(capability, expected_vip=self.broker_vip, egress_mode="status-quo")
        return capability

    def execute(self, _plan, instances: list[dict]) -> None:
        try:
            outputs = {instance["uuid"]: instance for instance in instances}
            if len(outputs) != len(instances) or any(
                f"{binding.target_address}#0" not in outputs for binding in self.bindings
            ):
                raise ValueError
            for binding in self.bindings:
                instance = outputs[f"{binding.target_address}#0"]
                execution = build_guest_execution_context(instance, os_type="linux", role="raes-node")
                try:
                    # SSM stores command payloads. Enrollment requires a private
                    # stdin channel and verified SSH host identity instead.
                    if execution.transport_name != "ssh" or not execution.wait_for_ready(timeout_seconds=60):
                        raise ValueError
                    enrollment = self._issue(binding.allocation_id)
                    if enrollment.expires_at <= datetime.now(UTC):
                        raise ValueError
                    payload = json.dumps(
                        {
                            "broker_url": self.broker_url,
                            "ca_pem": self.ca_pem,
                            "enrollment_token": enrollment.enrollment_token.get_secret_value(),
                        }
                    )
                    result = execution.executor.run_command(
                        execution.target,
                        _install_script(binding.workload_role),
                        stdin_input=payload,
                        timeout_seconds=30,
                        document_name=execution.document_name,
                    )
                    if not result.success or result.exit_code != 0:
                        raise ValueError
                finally:
                    execution.close()
        except Exception:
            raise ModelEnrollmentError("Guest model enrollment failed") from None

    def _issue(self, allocation_id: UUID) -> ModelEnrollment:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=self.ca_pem)
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), _NoRedirect())
        identity = workload_assertion(
            provider=self.provider,
            region=self.region,
            audience=f"{self.audience}:enroll:{self.operation_id}",
        )
        if not _ORIGIN.fullmatch(self.control_url):
            raise ValueError
        request = Request(  # noqa: S310 - closed HTTPS origin, no redirects or environment proxies.
            self.control_url + "/control/v1/enroll",
            data=json.dumps({"allocation_id": str(allocation_id), "operation_id": str(self.operation_id)}).encode(),
            headers={"Content-Type": "application/json", "Authorization": identity},
            method="POST",
        )
        with opener.open(request, timeout=5) as response:
            if response.status != 200:
                raise ValueError
            return ModelEnrollment.model_validate(strict_json(response.read(8193), limit=8192))


def load_model_enrollment(run) -> EnrollmentDelivery | None:
    """Validate execution coordinates before any cloud mutation; tokens come later."""
    if not run.input.model_enrollments:
        return None
    try:
        ca = base64.b64decode(os.environ["MODEL_ENROLLMENT_CA_PEM_B64"], validate=True).decode("ascii")
        if len(ca) > 16384:
            raise ValueError
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=ca)
        control, broker = os.environ["MODEL_ENROLLMENT_CONTROL_URL"], os.environ["MODEL_BROKER_GUEST_URL"]
        if not _ORIGIN.fullmatch(control) or not _ORIGIN.fullmatch(broker):
            raise ValueError
        provider = os.environ["CLOUD_PROVIDER"]
        if provider not in {"aws", "gcp"}:
            raise ValueError
        region = os.environ.get("AWS_REGION" if provider == "aws" else "CLOUD_REGION", "")
        if provider == "aws" and not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", region):
            raise ValueError
        delivery = EnrollmentDelivery(
            operation_id=UUID(run.operation_id),
            bindings=run.input.model_enrollments,
            provider=provider,
            region=region,
            control_url=control,
            audience="shifter-model-control",
            broker_url=broker,
            ca_pem=ca,
            broker_vip=os.environ.get("MODEL_BROKER_GUEST_VIP", "") if provider == "gcp" else "",
        )
        if provider == "gcp":
            delivery.gce_egress_capability()
        return delivery
    except Exception:
        raise ModelEnrollmentError("Guest model enrollment configuration is unavailable") from None


def _install_script(role: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", role):
        raise ValueError
    encoded = base64.b64encode(files("shifter_adapter_sdk").joinpath("model_access.py").read_bytes()).decode("ascii")
    directory = f"/run/shifter-model-access/{role}"
    # The script is public; the short-lived token travels on a distinct stdin
    # channel. Runtime paths are root-owned tmpfs, never image or disk state.
    return (
        "set -euo pipefail\numask 077\n"
        'test "$(findmnt -n -o FSTYPE -T /run)" = tmpfs\n'
        "test ! -L /run/shifter-model-access\n"
        "install -d -m 0700 -o root -g root /run/shifter-model-access\n"
        f"test ! -L {directory}\ninstall -d -m 0700 -o root -g root {directory}\n"
        f"printf %s '{encoded}' | base64 -d > {directory}/helper.py\n"
        f"python3 {directory}/helper.py enroll --state {directory}/session.json >/dev/null 2>&1\n"
    )
