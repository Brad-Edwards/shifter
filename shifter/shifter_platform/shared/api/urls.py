"""Shared, cross-domain platform API routes (#1374).

Mounted from ``config/api_urls.py`` alongside the per-domain API includes.
Currently owns only the audit read endpoint; the router is kept even for one
route so future shared, cross-domain endpoints have an obvious home.
"""

from __future__ import annotations

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from shared.api import audit

router = DefaultRouter()
router.register(r"audit", audit.AuditLogViewSet, basename="auditlog")

urlpatterns = [
    path("", include(router.urls)),
]
