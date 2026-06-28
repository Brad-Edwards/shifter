"""Canonical /api/v1 CMS API routes."""

from __future__ import annotations

from django.conf import settings
from django.urls import path

from cms.api import views

app_name = "cms"

urlpatterns = [
    path("scenario-editor/validate-yaml/", views.YAMLValidateView.as_view(), name="scenario-editor-validate-yaml"),
    path(
        "scenario-editor/scenarios/from-yaml/",
        views.YAMLScenarioCreateView.as_view(),
        name="scenario-editor-scenario-create-yaml",
    ),
]

if settings.EXPERIMENTS_ENABLED:
    urlpatterns += [
        path(
            "experiments/scenarios/<str:scenario_id>/instances/",
            views.ScenarioInstancesView.as_view(),
            name="scenario-instances",
        ),
        path(
            "experiments/scripts/upload/initiate/",
            views.ScriptUploadInitiateView.as_view(),
            name="script-upload-initiate",
        ),
        path(
            "experiments/scripts/upload/complete/",
            views.ScriptUploadCompleteView.as_view(),
            name="script-upload-complete",
        ),
    ]
