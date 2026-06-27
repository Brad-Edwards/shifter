"""Invite token exchange and pending-invite completion (#469)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

from django.db import transaction
from django.utils import timezone

from ctf.enums import ParticipantStatus
from ctf.models import CTFParticipant
from ctf.services.participant.lifecycle import _set_ctf_participant_profile

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import HttpRequest

_MAX_INVITE_TOKEN_LEN = 256
_DASHBOARD_URL_NAME = "mission_control:dashboard"


@dataclass(frozen=True)
class InviteExchangeResult:
    """Outcome of an invite-token exchange or pending-invite completion."""

    redirect: str | None = None
    error: str | None = None
    http_status: int = 200
    requires_login: bool = False
    login_url: str | None = None


@dataclass(frozen=True)
class _TokenExchangeOutcome:
    """Internal result from processing a locked invite row."""

    result: InviteExchangeResult | None = None
    user_to_login: User | None = None
    redirect_url: str | None = None


def _invalid_invite_result(*, http_status: int = 400) -> InviteExchangeResult:
    """Return a standard invalid-invite response."""
    return InviteExchangeResult(error="Invalid invite token.", http_status=http_status)


def _dashboard_url() -> str:
    """Return the post-enrollment Mission Control dashboard URL."""
    from django.urls import reverse

    return reverse(_DASHBOARD_URL_NAME)


def _clear_pending_invite_session(request: HttpRequest) -> None:
    """Drop any in-flight existing-account invite binding from the session."""
    request.session.pop("ctf_pending_invite_id", None)
    request.session.pop("ctf_pending_invite_user_id", None)
    request.session.modified = True


def _store_pending_invite_session(request: HttpRequest, participant: CTFParticipant, existing_user: User) -> None:
    """Bind a burned invite to the matching Django user for post-login completion."""
    request.session["ctf_pending_invite_id"] = str(participant.pk)
    request.session["ctf_pending_invite_user_id"] = str(existing_user.pk)
    request.session.modified = True


def _burn_invite_token(participant: CTFParticipant) -> None:
    """Replace the invite token so the submitted credential cannot be reused."""
    participant.invite_token = secrets.token_urlsafe(32)
    participant.invite_token_expires = timezone.now()
    participant.save(update_fields=["invite_token", "invite_token_expires", "updated_at"])


def _create_user_for_participant(participant: CTFParticipant) -> User:
    """Create a passwordless Django user for a new invite recipient."""
    from django.contrib.auth.models import User

    user = User.objects.create_user(
        username=participant.email,
        email=participant.email,
        first_name=participant.name.split()[0] if participant.name else "",
        last_name=" ".join(participant.name.split()[1:]) if participant.name else "",
    )
    user.set_unusable_password()
    user.save()
    return user


def _register_participant_with_user(participant: CTFParticipant, user: User) -> None:
    """Link the participant row to a Django user and apply CTF profile state."""
    participant.user = user
    participant.status = ParticipantStatus.REGISTERED.value
    participant.registered_at = timezone.now()
    participant.save(update_fields=["user", "status", "registered_at", "updated_at"])
    _set_ctf_participant_profile(user, participant.event)


def _normalize_invite_token(token: str) -> tuple[str, InviteExchangeResult | None]:
    """Strip and validate invite token input."""
    normalized = token.strip()
    if not normalized:
        return normalized, InviteExchangeResult(error="Missing invite token.", http_status=400)
    if len(normalized) > _MAX_INVITE_TOKEN_LEN:
        return normalized, _invalid_invite_result()
    return normalized, None


def _locked_participant_for_token(token: str) -> CTFParticipant | None:
    """Load and validate the participant row for a submitted invite token."""
    participant = CTFParticipant.objects.select_for_update().filter(invite_token=token).select_related("event").first()
    if participant is None or not participant.is_invite_valid or participant.user_id is not None:
        return None
    return participant


def _existing_user_login_required_result(
    request: HttpRequest,
    participant: CTFParticipant,
    existing_user: User,
) -> InviteExchangeResult:
    """Burn the token and require platform login before enrollment completes."""
    from django.urls import reverse

    _burn_invite_token(participant)
    _store_pending_invite_session(request, participant, existing_user)
    login_path = reverse("platform_login")
    next_path = quote(reverse("ctf:ctf_register"), safe="")
    return InviteExchangeResult(
        error="Sign in with your existing account to accept this invitation.",
        http_status=401,
        requires_login=True,
        login_url=f"{login_path}?next={next_path}",
    )


def _process_existing_user_invite(
    request: HttpRequest,
    participant: CTFParticipant,
    existing_user: User,
) -> _TokenExchangeOutcome:
    """Complete or defer enrollment when the invite email already has a Django user."""
    if not request.user.is_authenticated:
        return _TokenExchangeOutcome(result=_existing_user_login_required_result(request, participant, existing_user))
    if request.user.pk != existing_user.pk:
        return _TokenExchangeOutcome(result=_invalid_invite_result())
    _register_participant_with_user(participant, request.user)
    _burn_invite_token(participant)
    _clear_pending_invite_session(request)
    return _TokenExchangeOutcome(redirect_url=_dashboard_url())


def _process_new_user_invite(participant: CTFParticipant) -> _TokenExchangeOutcome:
    """Create a Django user and complete enrollment for a first-time invite recipient."""
    user = _create_user_for_participant(participant)
    _register_participant_with_user(participant, user)
    _burn_invite_token(participant)
    return _TokenExchangeOutcome(user_to_login=user, redirect_url=_dashboard_url())


def exchange_invite_token(request: HttpRequest, token: str) -> InviteExchangeResult:
    """Validate and consume an invite token, onboarding the participant when allowed."""
    from django.contrib.auth import login
    from django.contrib.auth.models import User

    token, early_error = _normalize_invite_token(token)
    if early_error is not None:
        return early_error

    outcome = _TokenExchangeOutcome()
    with transaction.atomic():
        participant = _locked_participant_for_token(token)
        if participant is None:
            outcome = _TokenExchangeOutcome(result=_invalid_invite_result())
        else:
            existing_user = User.objects.filter(email__iexact=participant.email).first()
            if existing_user is not None:
                outcome = _process_existing_user_invite(request, participant, existing_user)
            else:
                outcome = _process_new_user_invite(participant)

    if outcome.result is not None:
        return outcome.result

    if outcome.user_to_login is not None:
        login(request, outcome.user_to_login, backend="django.contrib.auth.backends.ModelBackend")

    return InviteExchangeResult(redirect=outcome.redirect_url)


def _pending_session_error(request: HttpRequest) -> InviteExchangeResult | None:
    """Validate session-bound pending invite state before completion."""
    pending_id = request.session.get("ctf_pending_invite_id")
    pending_user_id = request.session.get("ctf_pending_invite_user_id")
    error: InviteExchangeResult | None = None
    if not pending_id or not pending_user_id:
        error = InviteExchangeResult(error="No pending invitation.", http_status=400)
    elif not request.user.is_authenticated:
        error = InviteExchangeResult(error="Authentication required.", http_status=401)
    elif request.user.pk != int(pending_user_id):
        error = _invalid_invite_result()
    return error


def _load_pending_participant(
    request: HttpRequest,
    pending_id: str,
) -> tuple[CTFParticipant | None, InviteExchangeResult | None]:
    """Load the locked participant row referenced by the pending-invite session."""
    try:
        participant = CTFParticipant.objects.select_for_update().select_related("event").get(pk=pending_id)
    except CTFParticipant.DoesNotExist:
        _clear_pending_invite_session(request)
        return None, _invalid_invite_result()
    return participant, None


def _pending_participant_state_error(
    request: HttpRequest,
    participant: CTFParticipant,
) -> InviteExchangeResult | None:
    """Reject completion when the invite is no longer eligible."""
    if participant.user_id is not None or participant.status != ParticipantStatus.INVITED.value:
        _clear_pending_invite_session(request)
        return _invalid_invite_result()
    return None


def _complete_pending_for_authenticated_user(
    request: HttpRequest,
    participant: CTFParticipant,
) -> InviteExchangeResult | None:
    """Verify the logged-in user matches the invite and finish enrollment."""
    from django.contrib.auth.models import User

    existing_user = User.objects.filter(email__iexact=participant.email).first()
    if existing_user is None or existing_user.pk != request.user.pk:
        return _invalid_invite_result()
    _register_participant_with_user(participant, request.user)
    _clear_pending_invite_session(request)
    return None


def _complete_pending_invite_in_transaction(
    request: HttpRequest,
    pending_id: str,
) -> tuple[InviteExchangeResult | None, str | None]:
    """Finish a pending invite under the participant row lock."""
    participant, load_error = _load_pending_participant(request, pending_id)
    error = load_error
    redirect_url: str | None = None

    if error is None:
        assert participant is not None
        error = _pending_participant_state_error(request, participant)

    if error is None:
        assert participant is not None
        error = _complete_pending_for_authenticated_user(request, participant)

    if error is None:
        redirect_url = _dashboard_url()

    return error, redirect_url


def complete_pending_invite(request: HttpRequest) -> InviteExchangeResult:
    """Finish onboarding for an existing account after platform login."""
    result = _pending_session_error(request)
    redirect_url: str | None = None

    if result is None:
        pending_id = request.session["ctf_pending_invite_id"]
        with transaction.atomic():
            result, redirect_url = _complete_pending_invite_in_transaction(request, pending_id)

    if result is not None:
        return result
    return InviteExchangeResult(redirect=redirect_url)
