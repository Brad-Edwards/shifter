"""Workspace scope resolution for the CMS launch boundary (#1325, ADR-046-R3).

One place decides which workspace a launch belongs to. Both launch paths -- the
cyberscript ``create_range`` and the ACES-native ``create_aces_native_range`` --
call this, so scope is never resolved in a view, a serializer, or the
provisioner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import User


def resolve_launch_workspace(user: User) -> int:
    """Resolve the workspace scope a launch by ``user`` belongs to.

    #1325 resolves the launcher's personal workspace, which preserves current
    single-user behavior exactly while giving every new range a real tenancy
    binding. #1327 replaces this with workspace selection and admission; keeping
    the decision here means that change lands in one function rather than at
    every call site.
    """
    from workspaces.services import resolve_personal_workspace

    return resolve_personal_workspace(user).workspace_id
