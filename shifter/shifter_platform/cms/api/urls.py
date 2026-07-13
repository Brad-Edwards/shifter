"""Canonical /api/v1 CMS API routes."""

from __future__ import annotations

from django.urls import path

from cms.api import views

app_name = "cms"

urlpatterns = [
    path("catalog/", views.CatalogListView.as_view(), name="catalog-list"),
    path("catalog/<slug:scenario_id>/", views.CatalogDetailView.as_view(), name="catalog-detail"),
    path("scenario-editor/validate-yaml/", views.YAMLValidateView.as_view(), name="scenario-editor-validate-yaml"),
    # Structured create + YAML create. `from-yaml/` is declared before the
    # `<slug:scenario_id>/` detail route so it is not captured as a scenario id.
    path("scenario-editor/scenarios/", views.ScenarioCreateView.as_view(), name="scenario-editor-scenario-create"),
    path(
        "scenario-editor/scenarios/from-yaml/",
        views.YAMLScenarioCreateView.as_view(),
        name="scenario-editor-scenario-create-yaml",
    ),
    # Per-scenario sub-actions before the bare detail route (same slug-capture reason).
    path(
        "scenario-editor/scenarios/<slug:scenario_id>/clone/",
        views.ScenarioCloneView.as_view(),
        name="scenario-editor-scenario-clone",
    ),
    path(
        "scenario-editor/scenarios/<slug:scenario_id>/metadata/",
        views.ScenarioMetadataView.as_view(),
        name="scenario-editor-scenario-metadata",
    ),
    path(
        "scenario-editor/scenarios/<slug:scenario_id>/export/",
        views.ScenarioExportView.as_view(),
        name="scenario-editor-scenario-export",
    ),
    path(
        "scenario-editor/scenarios/<slug:scenario_id>/",
        views.ScenarioResourceView.as_view(),
        name="scenario-editor-scenario-detail",
    ),
]
