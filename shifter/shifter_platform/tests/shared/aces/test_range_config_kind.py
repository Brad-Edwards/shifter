"""Unit tests for the persisted range_config ACES discriminator (#1310).

``is_aces_provisioning_plan`` decides ACES-vs-legacy lifecycle from the persisted,
validated ``range_config.kind`` (ADR-031-R6). It positively identifies an ACES
plan and treats everything else -- ``None``, an empty dict, a cyberscript
wrapped-spec envelope, an unknown ``kind``, or a non-dict -- as legacy.
"""

from __future__ import annotations

from shared.aces.runtime_target import ACES_PROVISIONING_PLAN_KIND, is_aces_provisioning_plan


def test_identifies_aces_plan():
    assert is_aces_provisioning_plan({"kind": ACES_PROVISIONING_PLAN_KIND}) is True
    assert is_aces_provisioning_plan({"kind": ACES_PROVISIONING_PLAN_KIND, "resources": {}}) is True


def test_treats_non_aces_as_legacy():
    assert is_aces_provisioning_plan(None) is False
    assert is_aces_provisioning_plan({}) is False
    assert is_aces_provisioning_plan({"spec_schema": "range_spec", "payload": {}}) is False
    assert is_aces_provisioning_plan({"kind": "something_else"}) is False
    assert is_aces_provisioning_plan("not-a-dict") is False
