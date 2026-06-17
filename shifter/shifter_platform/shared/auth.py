"""Access control utilities for Shifter views and CMS authoring services."""

from __future__ import annotations

import contextlib
import functools
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from shared.constants import USER_CANNOT_BE_NONE, USER_MUST_BE_SAVED
from shared.log_sanitize import safe_log_value

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

THREAT_RESEARCH_GROUP = "Threat Research"
CTF_ORGANIZER_GROUP = "CTF Organizer"
CTF_PARTICIPANT_GROUP = "CTF Participant"

# Attribute used to memoize a user's group names for the duration of a single
# request. Django builds a fresh ``request.user`` per request, so caching here
# is strictly request-scoped — see ``get_user_group_names``.
_GROUP_NAMES_CACHE_ATTR = "_shifter_request_group_names"


def get_user_group_names(user: User) -> frozenset[str]:
    """Return the user's group names, memoized on the user instance.

    The portal context processors evaluate group membership up to five times per
    authenticated HTML render (``is_ctf_participant_only`` twice,
    ``can_edit_cms_authoring`` once, and the two ``get_user_role`` checks).
    Each of those previously issued its own ``auth_user_groups`` query. Caching
    the resolved group-name set on the per-request user instance collapses them
    to a single query without a stale cross-request cache (#898).

    The ``isinstance`` guard keeps the memoization correct for test doubles and
    Django ``SimpleLazyObject`` proxies: a non-frozenset cached value (e.g. a
    ``MagicMock`` auto-attribute) is treated as "not cached" and recomputed.
    """
    cached = getattr(user, _GROUP_NAMES_CACHE_ATTR, None)
    if isinstance(cached, frozenset):
        return cached
    names = frozenset(user.groups.values_list("name", flat=True))
    # Some user-like objects reject attribute writes; recompute next time if so.
    with contextlib.suppress(AttributeError, TypeError):
        setattr(user, _GROUP_NAMES_CACHE_ATTR, names)
    return names


def is_ctf_organizer(user) -> bool:
    """Return True if the user is in the CTF Organizer group."""
    if not user.is_active:
        return False
    return CTF_ORGANIZER_GROUP in get_user_group_names(user)


def is_ctf_participant(user) -> bool:
    """Return True if the user is in the CTF Participant group."""
    if not user.is_active:
        return False
    return CTF_PARTICIPANT_GROUP in get_user_group_names(user)


def is_ctf_participant_only(user) -> bool:
    """Return True if the user has no platform role that grants Launch Range.

    CTF roles (Participant, Organizer) do NOT grant Launch Range access.
    Only staff, superuser, or Threat Research group grants it.

    A user is "CTF only" when they:
    - ARE in a CTF group (Participant or Organizer)
    - Are NOT staff or superuser
    - Are NOT in Threat Research group
    """
    if not user.is_active:
        return False
    if user.is_staff or user.is_superuser:
        return False
    user_groups = get_user_group_names(user)
    has_ctf_role = bool(user_groups & {CTF_PARTICIPANT_GROUP, CTF_ORGANIZER_GROUP})
    if not has_ctf_role:
        return False
    return THREAT_RESEARCH_GROUP not in user_groups


def can_edit_cms_authoring(user) -> bool:
    """Return True if the user may use the CMS authoring surfaces.

    Canonical policy for the experiment and scenario editor: an active user
    who is either staff or a member of the ``Threat Research`` group. This
    predicate is the single source of truth for view decorators, service-layer
    gates, and template context — service-layer authorization MUST consume
    this rather than re-implementing the group check locally.
    """
    if not user.is_active:
        return False
    if user.is_staff:
        return True
    return THREAT_RESEARCH_GROUP in get_user_group_names(user)


def validate_cms_authoring_user(user, func_name: str) -> None:
    """Validate user shape and CMS authoring authorization in one step.

    Combines the structural user-presence checks (None / instance / saved)
    used across CMS service modules with the canonical authorization
    predicate so experiment- and scenario-editor service entrypoints share a
    single validator. The wrapper exists so every CMS authoring service
    module collapses to a one-line delegation, eliminating the historical
    near-duplicate ``_validate_user`` bodies.
    """
    if user is None:
        logger.error("%s called with None user", func_name)
        raise TypeError(USER_CANNOT_BE_NONE)
    if not hasattr(user, "id"):
        logger.error(
            "%s called with invalid user type: %s",
            func_name,
            type(user).__name__,
        )
        raise TypeError(f"user must be a User instance, got {type(user).__name__}")
    if user.id is None:
        logger.error("%s called with unsaved user (id=None)", func_name)
        raise ValueError(USER_MUST_BE_SAVED)
    if not can_edit_cms_authoring(user):
        logger.warning("%s denied: user_id=%s not staff or Threat Research", func_name, user.id)
        raise PermissionDenied("Active staff or Threat Research group membership is required")


def threat_research_required(view_func: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Decorator that restricts access to staff and Threat Research group members.

    - Unauthenticated users are redirected to LOGIN_URL.
    - Authenticated users without permission are redirected to the dashboard
      with an error message.
    """

    @functools.wraps(view_func)
    def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not request.user.is_authenticated:
            logger.debug("threat_research_required: unauthenticated user, redirecting to login")
            return redirect(settings.LOGIN_URL)

        if can_edit_cms_authoring(request.user):
            return view_func(request, *args, **kwargs)

        logger.warning(
            "threat_research_required: user %s denied access to %s",
            request.user.pk,
            safe_log_value(request.path),
        )
        messages.error(request, "You do not have permission to access this page.")
        return redirect("mission_control:dashboard")

    return _wrapped
