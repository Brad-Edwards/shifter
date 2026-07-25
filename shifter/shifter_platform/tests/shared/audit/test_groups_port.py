"""Tests for the neutral Cognito-groups provider port and its fail-closed binding (#1374)."""

from __future__ import annotations

import pytest

from shared.audit.groups_port import (
    CognitoGroupsProvider,
    CognitoGroupsProviderBindingError,
    bind_cognito_groups_provider,
    get_cognito_groups_provider,
    reset_cognito_groups_provider,
)


class _RecordingProvider:
    """Minimal CognitoGroupsProvider implementation for tests."""

    def __init__(self, groups: list[str] | None = None) -> None:
        self.groups = groups or []

    def groups_for_user(self, user: object) -> list[str]:
        return self.groups


@pytest.fixture(autouse=True)
def _restore_binding():
    """Save and restore the real startup binding around each test."""
    try:
        original = get_cognito_groups_provider()
    except CognitoGroupsProviderBindingError:
        original = None
    reset_cognito_groups_provider()
    yield
    reset_cognito_groups_provider()
    if original is not None:
        bind_cognito_groups_provider(original)


def test_get_without_binding_raises():
    with pytest.raises(CognitoGroupsProviderBindingError):
        get_cognito_groups_provider()


def test_bind_then_get_returns_same_instance():
    provider = _RecordingProvider(["security"])
    bind_cognito_groups_provider(provider)
    assert get_cognito_groups_provider() is provider


def test_binding_same_instance_twice_is_idempotent():
    provider = _RecordingProvider()
    bind_cognito_groups_provider(provider)
    bind_cognito_groups_provider(provider)  # no error
    assert get_cognito_groups_provider() is provider


def test_binding_conflicting_instance_fails_closed():
    bind_cognito_groups_provider(_RecordingProvider())
    other = _RecordingProvider()
    with pytest.raises(CognitoGroupsProviderBindingError):
        bind_cognito_groups_provider(other)


def test_reset_clears_binding():
    bind_cognito_groups_provider(_RecordingProvider())
    reset_cognito_groups_provider()
    with pytest.raises(CognitoGroupsProviderBindingError):
        get_cognito_groups_provider()


def test_recording_provider_satisfies_protocol():
    assert isinstance(_RecordingProvider(), CognitoGroupsProvider)
