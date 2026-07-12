"""Platform-local authentication boundaries."""

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpRequest

from management.services import is_temporary_ctf_account


class _ProfileAwareModelBackend(ModelBackend):
    """Load the durable origin marker with the authenticated session user."""

    def get_user(self, user_id: int) -> User | None:
        user_model = get_user_model()
        try:
            user = user_model._default_manager.select_related("profile").get(pk=user_id)
        except user_model.DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None


class PlatformModelBackend(_ProfileAwareModelBackend):
    """Django password backend that excludes temporary CTF identities."""

    def user_can_authenticate(self, user: User | AnonymousUser | None) -> bool:
        return user is not None and not is_temporary_ctf_account(user) and super().user_can_authenticate(user)


class CTFParticipantBackend(_ProfileAwareModelBackend):
    """Local password backend reachable only from the dedicated CTF view."""

    def authenticate(
        self,
        request: HttpRequest | None,
        username: str | None = None,
        password: str | None = None,
        **kwargs: Any,
    ) -> User | None:
        if kwargs.pop("ctf_participant", False) is not True:
            return None
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            return None
        from ctf.services.participant.accounts import live_participant_for_user

        return user if live_participant_for_user(user) is not None else None

    def user_can_authenticate(self, user: User | AnonymousUser | None) -> bool:
        return user is not None and is_temporary_ctf_account(user) and super().user_can_authenticate(user)
