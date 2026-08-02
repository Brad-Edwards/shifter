"""Workspaces app configuration."""

from django.apps import AppConfig


class WorkspacesConfig(AppConfig):
    """Django app config for the organization/workspace tenancy domain."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "workspaces"
    verbose_name = "Workspaces"
