"""CTF-owned assembly of trusted signed-receipt validation context."""

from __future__ import annotations

from uuid import UUID

from ctf.exceptions import CTFValidationError
from shared.receipt_validation import ReceiptSubmissionContext, ReceiptValidationContext


def resolve_receipt_validation_context(
    submission: ReceiptSubmissionContext,
    *,
    profile_id: str,
    objective_id: str,
) -> ReceiptValidationContext:
    """Resolve and cross-check one exact CTF→CMS→Engine assignment binding."""
    from ctf.bridges import cms_project_ctf_receipt_binding
    from ctf.validators import get_receipt_profile

    profile = get_receipt_profile(profile_id)
    if profile is None or objective_id not in profile.permitted_objectives:
        raise CTFValidationError("Receipt verifier profile is unavailable")
    _assert_unambiguous_objective_binding(
        event_id=submission.event_id,
        challenge_id=submission.challenge_id,
        profile_id=profile_id,
        objective_id=objective_id,
        lock=False,
    )
    if submission.range_instance_id is None:
        raise CTFValidationError("Receipt range binding is unavailable")
    try:
        binding = cms_project_ctf_receipt_binding(
            submission.range_instance_id,
            owner_user_id=submission.owner_user_id,
            event_id=submission.event_id,
            participant_id=submission.participant_id,
            profile_id=profile_id,
            objective_id=objective_id,
        )
    except Exception as exc:
        raise CTFValidationError("Receipt range binding is unavailable") from exc
    if not (
        binding.deployment_id == profile.deployment_id
        and binding.provider_contract == profile.provider_contract
        and binding.issuer_id == profile.issuer_id
        and binding.key_mode in profile.allowed_key_modes
        and binding.algorithm_id in profile.allowed_algorithms
    ):
        raise CTFValidationError("Receipt verifier registration does not match its deployment profile")
    return ReceiptValidationContext(
        deployment_id=binding.deployment_id,
        profile_id=binding.profile_id,
        provider_contract=binding.provider_contract,
        event_id=submission.event_id,
        participant_id=submission.participant_id,
        challenge_id=submission.challenge_id,
        range_instance_id=submission.range_instance_id,
        materialization_id=binding.materialization_id,
        assignment_epoch=binding.assignment_epoch,
        registration_revision=binding.registration_revision,
        provider_range_namespace=binding.provider_range_namespace,
        provider_participant_namespace=binding.provider_participant_namespace,
        issuer_id=binding.issuer_id,
        key_mode=binding.key_mode,
        algorithm_id=binding.algorithm_id,
        key_id=binding.key_id,
        public_verification_key=binding.public_verification_key,
        reset_generation=binding.reset_generation,
        registered_objectives=binding.objectives,
        objective_id=objective_id,
    )


def revalidate_receipt_context(context: ReceiptValidationContext) -> None:
    """Fail if the callback's exact registration is no longer authoritative."""
    from ctf.bridges import cms_confirm_ctf_receipt_binding
    from ctf.validators import get_receipt_profile

    owner_user_id = _participant_owner_id(context)
    profile = get_receipt_profile(context.profile_id)
    if profile is None or not (
        context.deployment_id == profile.deployment_id
        and context.provider_contract == profile.provider_contract
        and context.issuer_id == profile.issuer_id
        and context.key_mode in profile.allowed_key_modes
        and context.algorithm_id in profile.allowed_algorithms
        and context.objective_id in profile.permitted_objectives
    ):
        raise CTFValidationError("Receipt registration is no longer active")
    _assert_unambiguous_objective_binding(
        event_id=context.event_id,
        challenge_id=context.challenge_id,
        profile_id=context.profile_id,
        objective_id=context.objective_id,
        lock=True,
    )
    from shared.receipt_validation import ReceiptVerifierBinding

    expected = ReceiptVerifierBinding(
        registration_revision=context.registration_revision,
        materialization_id=context.materialization_id,
        assignment_epoch=context.assignment_epoch,
        deployment_id=context.deployment_id,
        profile_id=context.profile_id,
        provider_contract=context.provider_contract,
        ctf_event_id=context.event_id,
        ctf_participant_id=context.participant_id,
        objectives=context.registered_objectives,
        issuer_id=context.issuer_id,
        key_mode=context.key_mode,
        algorithm_id=context.algorithm_id,
        key_id=context.key_id,
        public_verification_key=context.public_verification_key,
        provider_range_namespace=context.provider_range_namespace,
        provider_participant_namespace=context.provider_participant_namespace,
        reset_generation=context.reset_generation,
    )
    try:
        cms_confirm_ctf_receipt_binding(
            context.range_instance_id,
            owner_user_id=owner_user_id,
            objective_id=context.objective_id,
            expected=expected,
        )
    except Exception as exc:
        raise CTFValidationError("Receipt registration is no longer active") from exc


def _participant_owner_id(context: ReceiptValidationContext) -> int:
    """Reload the locked CTF participant's server-owned user identity."""
    from ctf.models import CTFParticipant

    participant = CTFParticipant.objects.get(pk=context.participant_id)
    if participant.user_id is None:
        raise CTFValidationError("Receipt registration is no longer active")
    return participant.user_id


def _assert_unambiguous_objective_binding(
    *,
    event_id: UUID,
    challenge_id: UUID,
    profile_id: str,
    objective_id: str,
    lock: bool,
) -> None:
    """Require one provider objective to name exactly one active challenge.

    PENR1 signs its provider-owned objective (``flag_id``), not a Shifter
    challenge UUID.  An organizer must therefore never be able to attach the
    same profile/objective pair to two challenges in an event and turn one
    receipt into two challenge authorizations.  The check is repeated under
    row locks immediately before commit so a live flag repair cannot race a
    verified receipt into a newly ambiguous mapping.
    """
    from ctf.models import CTFFlag

    flags = CTFFlag.objects.filter(
        challenge__event_id=event_id,
        challenge__deleted_at__isnull=True,
    ).only("challenge_id", "flag_type", "validator_config")
    if lock:
        flags = flags.select_for_update()
    matching_challenges = {
        flag.challenge_id
        for flag in flags
        if _receipt_selection(flag.flag_type, flag.validator_config) == (profile_id, objective_id)
    }
    if matching_challenges != {challenge_id}:
        raise CTFValidationError("Receipt objective is not uniquely bound to this challenge")


def _receipt_selection(flag_type: str, validator_config: object) -> tuple[str, str] | None:
    """Return a context-capable flag's canonical profile/objective selection."""
    if not isinstance(validator_config, dict):
        return None
    selection: object
    if flag_type == "http":
        selection = validator_config
    elif flag_type == "programmable":
        from ctf.validators import get_validator, validator_supports_server_context

        validator_name = validator_config.get("validator_name")
        if not isinstance(validator_name, str) or get_validator(validator_name) is None:
            return None
        if not validator_supports_server_context(validator_name):
            return None
        selection = validator_config.get("receipt")
    else:
        from ctf.extensions import flag_validator_supports_server_context, get_flag_validator

        if get_flag_validator(flag_type) is None or not flag_validator_supports_server_context(flag_type):
            return None
        selection = validator_config
    if not isinstance(selection, dict) or selection.get("protocol") != "receipt-v1":
        return None
    profile_id = selection.get("profile_id")
    objective_id = selection.get("objective_id")
    if not isinstance(profile_id, str) or not isinstance(objective_id, str):
        return None
    return profile_id, objective_id
