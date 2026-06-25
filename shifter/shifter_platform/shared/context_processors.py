"""Context processors for the shared app."""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpRequest

from risk_register.access import principal_has_risk_register_access
from shared.auth import can_edit_cms_authoring

logger = logging.getLogger(__name__)


def feature_flags(request: HttpRequest) -> dict[str, bool]:
    """Expose non-secret feature flags to templates (e.g. nav gating)."""
    return {
        # Experiments is half-built and off by default (#1195); the nav link must
        # not render when its routes aren't registered (config/urls.py).
        "experiments_enabled": bool(getattr(settings, "EXPERIMENTS_ENABLED", False)),
    }


def user_permissions(request: HttpRequest) -> dict[str, bool]:
    """Inject authorization flags into every template context."""
    if not request.user.is_authenticated:
        return {
            "can_access_threat_research": False,
            "can_access_risk_register": False,
        }

    can_access_risk_register = principal_has_risk_register_access(request)
    allowed = can_edit_cms_authoring(request.user)
    logger.debug(
        "user_permissions: user=%s can_access_threat_research=%s can_access_risk_register=%s",
        request.user.pk,
        allowed,
        can_access_risk_register,
    )
    return {
        "can_access_threat_research": allowed,
        "can_access_risk_register": can_access_risk_register,
    }
