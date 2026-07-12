"""Canonical /api/v1 CMS API routes."""

from __future__ import annotations

from django.urls import path

from cms.api import views

app_name = "cms"

urlpatterns = [
    path("catalog/", views.CatalogListView.as_view(), name="catalog-list"),
    # Must precede the ``catalog/<slug:scenario_id>/`` detail route: "packs" is a
    # valid slug and the detail route would otherwise shadow this collection.
    path("catalog/packs/", views.PackRegisterView.as_view(), name="catalog-pack-register"),
    path("catalog/<slug:scenario_id>/", views.CatalogDetailView.as_view(), name="catalog-detail"),
    path("scenario-editor/validate-yaml/", views.YAMLValidateView.as_view(), name="scenario-editor-validate-yaml"),
    path(
        "scenario-editor/scenarios/from-yaml/",
        views.YAMLScenarioCreateView.as_view(),
        name="scenario-editor-scenario-create-yaml",
    ),
]
