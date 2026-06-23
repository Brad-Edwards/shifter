"""Shared string constants for the experiment view modules.

Centralizing these keeps the user-facing flash message and the URL route
names single-sourced across the flow-focused view submodules instead of
repeating the literals in each file (SonarCloud S1192).
"""

from __future__ import annotations

# Generic flash message shown when an unexpected exception reaches a view.
UNEXPECTED_ERROR_MESSAGE = "An unexpected error occurred. Please try again."

# URL route names (``app_name:url_name``) used in view redirects.
ROUTE_EXPERIMENT_LIST = "experiments:experiment_list"
ROUTE_EXPERIMENT_CREATE = "experiments:experiment_create"
ROUTE_EXPERIMENT_DETAIL = "experiments:experiment_detail"
ROUTE_SCRIPT_LIST = "experiments:script_list"
ROUTE_SCRIPT_UPLOAD = "experiments:script_upload"
