"""Canonical /api/v1 CMS API routes."""

from __future__ import annotations

from django.urls import path

from cms.api import raes_image_registry, views

app_name = "cms"

urlpatterns = [
    path("catalog/", views.CatalogListView.as_view(), name="catalog-list"),
    # Must precede the ``catalog/<slug:scenario_id>/`` detail route: "packs" is a
    # valid slug and the detail route would otherwise shadow this collection.
    path("catalog/packs/", views.PackRegisterView.as_view(), name="catalog-pack-register"),
    path("catalog/<slug:scenario_id>/", views.CatalogDetailView.as_view(), name="catalog-detail"),
    # Canonical RAES image registry management surface (#1566).
    path(
        "raes-image-mappings/",
        raes_image_registry.RaesImageMappingListCreateView.as_view(),
        name="raes-image-mappings",
    ),
    path(
        "raes-image-mappings/disable/",
        raes_image_registry.RaesImageMappingDisableView.as_view(),
        name="raes-image-mappings-disable",
    ),
    path(
        "scenario-editor/scenarios/<slug:scenario_id>/metadata/",
        views.ScenarioMetadataView.as_view(),
        name="scenario-editor-scenario-metadata",
    ),
    path(
        "scenario-editor/scenarios/<slug:scenario_id>/realizability/",
        views.ScenarioRealizabilityView.as_view(),
        name="scenario-editor-scenario-realizability",
    ),
    path(
        "scenario-editor/scenarios/<slug:scenario_id>/",
        views.ScenarioResourceView.as_view(),
        name="scenario-editor-scenario-detail",
    ),
]
