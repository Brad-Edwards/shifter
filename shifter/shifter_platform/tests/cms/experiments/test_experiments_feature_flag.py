"""Experiments feature flag (#1195).

The experiments feature is half-built (its command executor was never finished),
so it is gated off by default behind ``EXPERIMENTS_ENABLED``: the routes are not
registered, the nav link does not render, and the run-launch path refuses to
start the (non-existent) executor.
"""

from __future__ import annotations

import importlib
from uuid import uuid4

import pytest
from django.test import override_settings
from django.urls import NoReverseMatch, clear_url_caches, reverse


def _reload_urlconf() -> None:
    import config.urls

    clear_url_caches()
    importlib.reload(config.urls)


def test_experiments_routes_registered_when_enabled():
    # The suite runs with EXPERIMENTS_ENABLED on (conftest), so the routes resolve.
    assert reverse("experiments:experiment_list") == "/mission-control/experiments/"


def test_experiments_routes_absent_when_disabled():
    # Production default: with the flag off, the experiments routes are not
    # registered, so the feature is unreachable.
    try:
        with override_settings(EXPERIMENTS_ENABLED=False):
            _reload_urlconf()
            with pytest.raises(NoReverseMatch):
                reverse("experiments:experiment_list")
    finally:
        # Rebuild the URLconf from the (restored) suite settings.
        _reload_urlconf()


@override_settings(EXPERIMENTS_ENABLED=False)
def test_start_experiment_task_short_circuits_before_launch_when_disabled():
    from cms.experiments import ecs

    # The disabled guard runs before argument validation and before the task
    # runner, so a valid call returns None (nothing launched) ...
    assert (
        ecs.start_experiment_task(experiment_id=1, run_id=1, request_id=uuid4(), command="execute", payload={}) is None
    )
    # ... and even invalid args return None instead of raising, proving the guard
    # short-circuits ahead of the executor path entirely.
    assert ecs.start_experiment_task(experiment_id=None, run_id=None, request_id=None, command="execute") is None


@override_settings(EXPERIMENTS_ENABLED=True)
def test_start_experiment_task_validates_args_when_enabled():
    # When enabled the guard does not short-circuit, so argument validation runs.
    from cms.experiments import ecs

    with pytest.raises(TypeError):
        ecs.start_experiment_task(experiment_id=None, run_id=1, request_id=uuid4(), command="execute")


def test_feature_flags_context_processor_reflects_setting():
    from shared.context_processors import feature_flags

    with override_settings(EXPERIMENTS_ENABLED=False):
        assert feature_flags(object()) == {"experiments_enabled": False}
    with override_settings(EXPERIMENTS_ENABLED=True):
        assert feature_flags(object()) == {"experiments_enabled": True}
