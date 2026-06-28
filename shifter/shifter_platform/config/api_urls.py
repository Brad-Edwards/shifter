"""Canonical v1 API URL configuration for the Shifter platform."""

from __future__ import annotations

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

app_name = "api"

urlpatterns = [
    path("schema/", SpectacularAPIView.as_view(api_version="v1"), name="openapi-schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="v1:openapi-schema"), name="api-docs"),
    path("ctf/", include("ctf.api.urls")),
    path("mission-control/", include("mission_control.api.urls")),
    path("", include("risk_register.api.urls")),
]
