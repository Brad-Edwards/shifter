"""Validated durable intents for privileged provisioner Job launches."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from engine.models import ProvisionerLaunchIntent, ProvisionerLaunchStatus
from shared.cloud import PROVISIONER_CONTAINER_NAME
from shared.cloud.gcp.base import build_idempotent_job_name

_OPERATIONS = {
    "range": {"provision", "destroy", "pause", "resume"},
    "aces-range": {"provision", "destroy", "pause", "resume"},
    "ngfw": {"provision", "deprovision", "start", "stop"},
}
PROVISIONER_DISPATCH_FAILED = "Provisioner dispatch failed"


def validate_provisioner_command(command: list[str]) -> dict[str, object]:
    """Return a versioned, secret-free payload for one canonical CLI command."""
    if not isinstance(command, list) or any(not isinstance(part, str) for part in command):
        raise ValueError("provisioner command must be a list of strings")
    if len(command) == 4 and command[2] == "--request-id":
        resource, operation, _, request_id = command
        if resource not in _OPERATIONS or operation not in _OPERATIONS[resource]:
            raise ValueError("unsupported provisioner resource or operation")
        try:
            parsed_request_id = UUID(request_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("request_id must be a UUID") from exc
        return {
            "version": 1,
            "resource": resource,
            "operation": operation,
            "request_id": str(parsed_request_id),
        }
    if len(command) == 6 and command[:2] in (["range", "provision"], ["range", "destroy"]):
        if command[2] != "--range-id" or command[4] != "--user-id":
            raise ValueError("legacy range command must use canonical identifier flags")
        try:
            range_id, user_id = int(command[3]), int(command[5])
        except ValueError as exc:
            raise ValueError("legacy identifiers must be integers") from exc
        if range_id < 0 or user_id < 0:
            raise ValueError("legacy identifiers must be non-negative")
        return {
            "version": 1,
            "resource": "range",
            "operation": command[1],
            "range_id": range_id,
            "user_id": user_id,
        }
    raise ValueError("command does not match a canonical provisioner launch shape")


def command_from_payload(payload: dict[str, object]) -> list[str]:
    """Reconstruct and revalidate the canonical command at the trust boundary."""
    if payload.get("version") != 1:
        raise ValueError("unsupported provisioner launch intent version")
    resource = payload.get("resource")
    operation = payload.get("operation")
    if "request_id" in payload:
        command = [str(resource), str(operation), "--request-id", str(payload["request_id"])]
    else:
        command = [
            str(resource),
            str(operation),
            "--range-id",
            str(payload.get("range_id")),
            "--user-id",
            str(payload.get("user_id")),
        ]
    if validate_provisioner_command(command) != payload:
        raise ValueError("provisioner launch intent payload is not canonical")
    return command


def _lock_for_generation(queryset: Any, expected_operation_id: UUID | str | None) -> Any:
    """Lock a domain projection when validating a persisted operation generation."""
    return queryset.select_for_update() if expected_operation_id is not None else queryset


def _require_current_generation(row: Any, expected_operation_id: UUID | str | None) -> None:
    """Reject an intent that belongs to a superseded domain-operation generation."""
    if expected_operation_id is not None and row.provisioner_operation_id != UUID(str(expected_operation_id)):
        raise ValueError("launch intent operation generation is no longer current")


def authorize_provisioner_payload(
    payload: dict[str, object],
    *,
    target: Any | None = None,
    expected_operation_id: UUID | str | None = None,
) -> None:
    """Fail closed unless current domain state authorizes the queued operation."""
    from engine.models import Instance, Range, Request

    resource = str(payload.get("resource"))
    operation = str(payload.get("operation"))
    if "request_id" not in payload:
        range_rows = _lock_for_generation(Range.objects.select_related("user"), expected_operation_id)
        row = target or range_rows.filter(pk=payload.get("range_id")).first()
        if row is None or row.user_id != payload.get("user_id"):
            raise ValueError("legacy launch intent does not match an owned range")
        _require_current_generation(row, expected_operation_id)
        if operation == "provision" and row.status not in {Range.Status.PENDING, Range.Status.PROVISIONING}:
            raise ValueError("range state does not authorize provisioning")
        if operation == "destroy" and row.status != Range.Status.DESTROYING:
            raise ValueError("range state does not authorize destruction")
        return

    request_id = UUID(str(payload["request_id"]))
    request = Request.objects.filter(request_id=request_id).first()
    if request is None:
        raise ValueError("launch intent request does not exist")
    if resource in {"range", "aces-range"}:
        range_rows = _lock_for_generation(Range.objects, expected_operation_id)
        range_row = target or range_rows.filter(request=request).first()
        if range_row is None:
            raise ValueError("launch intent request has no range")
        _require_current_generation(range_row, expected_operation_id)
        allowed_states = {
            "provision": {Range.Status.PENDING, Range.Status.PROVISIONING},
            "destroy": {Range.Status.DESTROYING},
            "pause": {Range.Status.PAUSING},
            "resume": {Range.Status.RESUMING},
        }
        if range_row.status not in allowed_states[operation]:
            raise ValueError("range state does not authorize the requested operation")
        return

    ngfw_rows = _lock_for_generation(Instance.objects, expected_operation_id)
    ngfw = target or ngfw_rows.filter(request=request, role=Instance.Role.NGFW).first()
    if ngfw is None:
        raise ValueError("launch intent request has no NGFW instance")
    _require_current_generation(ngfw, expected_operation_id)
    allowed_ngfw_states = {
        "provision": {"pending", "provisioning"},
        "deprovision": {"ready", "paused", "failed"},
        "start": {"paused", "failed"},
        "stop": {"ready"},
    }
    if ngfw.status not in allowed_ngfw_states[operation]:
        raise ValueError("NGFW state does not authorize the requested operation")


def _operation_identity(payload: dict[str, object]) -> UUID:
    """Return the stable identity of the current authorized domain operation."""
    from engine.models import Instance, Range, Request

    operation = f"{payload['resource']}:{payload['operation']}"
    with transaction.atomic():
        row: Range | Instance
        if "request_id" not in payload:
            row = Range.objects.select_for_update().get(pk=int(str(payload["range_id"])))
        else:
            request = Request.objects.get(request_id=UUID(str(payload["request_id"])))
            if payload["resource"] in {"range", "aces-range"}:
                row = Range.objects.select_for_update().get(request=request)
            else:
                row = Instance.objects.select_for_update().get(request=request, role=Instance.Role.NGFW)
        authorize_provisioner_payload(payload, target=row)
        current_intent = None
        if row.provisioner_operation_id is not None:
            current_intent = ProvisionerLaunchIntent.objects.filter(operation_id=row.provisioner_operation_id).first()
        retryable_generation = current_intent is not None and (
            current_intent.status == ProvisionerLaunchStatus.DLQ
            or (current_intent.status == ProvisionerLaunchStatus.SUCCEEDED and row.status == "failed")
        )
        if row.provisioner_operation != operation or row.provisioner_operation_id is None or retryable_generation:
            row.provisioner_operation = operation
            row.provisioner_operation_id = uuid4()
            row.save(update_fields=["provisioner_operation", "provisioner_operation_id"])
        operation_id = row.provisioner_operation_id
        if operation_id is None:  # pragma: no cover - guarded by the assignment above
            raise RuntimeError("failed to reserve a provisioner operation generation")
        return operation_id


def clear_provisioner_operation_after_failure(row: Any) -> list[str]:
    """Close a failed lifecycle episode so the same operation can be retried."""
    if row.provisioner_operation_id is None and not row.provisioner_operation:
        return []
    row.provisioner_operation = ""
    row.provisioner_operation_id = None
    return ["provisioner_operation", "provisioner_operation_id"]


def fail_current_provisioner_operation(
    payload: dict[str, object],
    expected_operation_id: UUID | str,
) -> bool:
    """Fail only the domain projection still owned by this operation generation."""
    from engine.models import App, Instance, Range, RangeEventOutbox, Request
    from shared.enums import ResourceStatus
    from shared.messages.events import EVENT_TYPE_STATUS_UPDATED

    try:
        command_from_payload(payload)
    except (KeyError, TypeError, ValueError):
        return False

    target: Range | Instance | None
    if "request_id" not in payload:
        target = Range.objects.select_for_update().filter(pk=int(str(payload["range_id"]))).first()
    else:
        request = Request.objects.filter(request_id=UUID(str(payload["request_id"]))).first()
        if request is None:
            return False
        if payload.get("resource") in {"range", "aces-range"}:
            target = Range.objects.select_for_update().filter(request=request).first()
        else:
            target = Instance.objects.select_for_update().filter(request=request, role=Instance.Role.NGFW).first()
    if target is None:
        return False
    try:
        authorize_provisioner_payload(
            payload,
            target=target,
            expected_operation_id=expected_operation_id,
        )
    except ValueError:
        # A newer generation or terminal lifecycle state won the race after
        # the provider failure. The stale intent must not overwrite it.
        return False

    target.status = ResourceStatus.FAILED.value
    update_fields = ["status", "updated_at"]
    update_fields.extend(clear_provisioner_operation_after_failure(target))
    if isinstance(target, Range):
        target.error_message = PROVISIONER_DISPATCH_FAILED
        update_fields.append("error_message")
    target.save(update_fields=update_fields)
    if isinstance(target, Range):
        event_id = uuid4()
        related_request = target.request
        request_id = (
            str(related_request.request_id) if related_request is not None else str(payload.get("request_id", ""))
        )
        event = {
            "event_type": EVENT_TYPE_STATUS_UPDATED,
            "event_id": str(event_id),
            "timestamp": timezone.now().isoformat(),
            "request_id": request_id,
            "range_id": target.id,
            "user_id": target.user_id,
            "new_status": ResourceStatus.FAILED.value,
            "error_message": PROVISIONER_DISPATCH_FAILED,
        }
        RangeEventOutbox.objects.create(
            event_id=event_id,
            event_type=EVENT_TYPE_STATUS_UPDATED,
            payload=event,
            next_attempt_at=timezone.now(),
        )
    if isinstance(target, Instance):
        App.objects.filter(instance=target).update(
            status=ResourceStatus.FAILED.value,
            updated_at=timezone.now(),
        )
    return True


def enqueue_provisioner_launch(command: list[str]) -> str:
    """Persist one durable intent per authorized operation and return its UUID."""
    payload = validate_provisioner_command(command)
    with transaction.atomic():
        operation_id = _operation_identity(payload)
        existing = ProvisionerLaunchIntent.objects.filter(operation_id=operation_id).first()
        if existing is not None:
            return str(existing.intent_id)
        canonical = f"{'|'.join(command)}|{operation_id}"
        idempotency_key = sha256(canonical.encode("utf-8")).hexdigest()
        existing = ProvisionerLaunchIntent.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return str(existing.intent_id)
        intent_id = uuid4()
        namespace = str(getattr(settings, "ENGINE_TASK_CLUSTER", "") or "")
        task_ref = (
            f"{namespace}/{build_idempotent_job_name(PROVISIONER_CONTAINER_NAME, str(intent_id))}" if namespace else ""
        )
        row = ProvisionerLaunchIntent.objects.create(
            intent_id=intent_id,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            payload=payload,
            task_ref=task_ref,
            next_attempt_at=timezone.now(),
        )
        return str(row.intent_id)


def task_ref_for_intent(intent_id: str) -> str:
    """Return the provider task reference reserved for a queued intent."""
    return ProvisionerLaunchIntent.objects.only("task_ref").get(intent_id=UUID(intent_id)).task_ref
