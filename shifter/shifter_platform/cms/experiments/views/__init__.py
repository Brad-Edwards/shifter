"""Experiment manager views.

All business logic is in experiments.services. Views handle HTTP only.
Views require staff or Threat Research group membership.

The implementation is split across private, flow-focused submodules
(``_scripts``, ``_experiments``, ``_downloads``, ``_ajax``) and re-exported
here as a stable public facade so ``from cms.experiments import views;
views.X`` (used by ``urls.py``) and ``from cms.experiments.views import X``
keep working.
"""

from __future__ import annotations

from cms.experiments.views._ajax import scenario_instances
from cms.experiments.views._downloads import artifact_download, experiment_download
from cms.experiments.views._experiments import (
    experiment_cancel,
    experiment_create,
    experiment_detail,
    experiment_list,
    experiment_start,
)
from cms.experiments.views._scripts import script_delete, script_list, script_upload

__all__ = [
    "artifact_download",
    "experiment_cancel",
    "experiment_create",
    "experiment_detail",
    "experiment_download",
    "experiment_list",
    "experiment_start",
    "scenario_instances",
    "script_delete",
    "script_list",
    "script_upload",
]
