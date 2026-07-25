"""Tests for the platform API-token scope registry (PLAT-102).

The scope registry is the central, pure vocabulary that both the DRF
permission layer (now) and the per-app function-view decorators (PLAT-106
migration) check against. These tests are pure-Python, no DB.
"""

from __future__ import annotations

import pytest

from shared.api_tokens import scopes


class TestKnownScopes:
    def test_risk_scopes_are_removed(self):
        # Risk Register was removed in #1374 Part B; its scopes must not
        # survive in the registry, and the constants themselves are gone so
        # new code cannot reference them.
        assert "risk:read" not in scopes.KNOWN_SCOPES
        assert "risk:write" not in scopes.KNOWN_SCOPES
        assert not hasattr(scopes, "RISK_READ")
        assert not hasattr(scopes, "RISK_WRITE")

    def test_migration_scopes_are_registered(self):
        # Mission Control scopes are enforced by PLAT-106 issue #1120; CTF/CMS
        # scopes remain registered for their follow-on migrations.
        for reserved in (
            "mission_control:range:read",
            "mission_control:range:write",
            "mission_control:upload:write",
            "mission_control:guacamole:read",
            "mission_control:ngfw:read",
            "mission_control:ngfw:write",
            "mission_control:credentials:write",
            "mission_control:vpn-profile:read",
            "ctf:event:read",
            "ctf:event:write",
            "ctf:play:read",
            "ctf:play:write",
            "ctf:vpn-profile:read",
            "cms:authoring:read",
            "cms:authoring:write",
        ):
            assert reserved in scopes.KNOWN_SCOPES

    def test_every_known_scope_follows_resource_operation_convention(self):
        # <resource>:<operation>, lowercase, no wildcards.
        for scope in scopes.KNOWN_SCOPES:
            assert scope == scope.lower()
            assert "*" not in scope
            assert scope.count(":") >= 1
            assert not scope.startswith(":") and not scope.endswith(":")


class TestValidateScopes:
    def test_normalizes_dedupes_and_sorts(self):
        result = scopes.validate_scopes(
            [
                scopes.MISSION_CONTROL_RANGE_WRITE,
                scopes.MISSION_CONTROL_RANGE_READ,
                scopes.MISSION_CONTROL_RANGE_WRITE,
            ]
        )
        assert result == [scopes.MISSION_CONTROL_RANGE_READ, scopes.MISSION_CONTROL_RANGE_WRITE]

    def test_rejects_empty_selection(self):
        with pytest.raises(scopes.InvalidScopeError):
            scopes.validate_scopes([])

    def test_rejects_unknown_scope(self):
        with pytest.raises(scopes.InvalidScopeError):
            scopes.validate_scopes([scopes.MISSION_CONTROL_RANGE_READ, "totally:bogus"])

    def test_rejects_a_retired_risk_scope(self):
        # A scope that was known before #1374 Part B (Risk Register removal)
        # is unknown now, not silently tolerated at mint time.
        with pytest.raises(scopes.InvalidScopeError):
            scopes.validate_scopes(["risk:read"])

    def test_rejects_wildcard(self):
        with pytest.raises(scopes.InvalidScopeError):
            scopes.validate_scopes(["*"])
        with pytest.raises(scopes.InvalidScopeError):
            scopes.validate_scopes(["mission_control:*"])

    def test_rejects_blank_entries(self):
        with pytest.raises(scopes.InvalidScopeError):
            scopes.validate_scopes([scopes.MISSION_CONTROL_RANGE_READ, "  "])


class TestHasScope:
    def test_granted_scope_is_present(self):
        granted = [scopes.MISSION_CONTROL_RANGE_READ, scopes.MISSION_CONTROL_RANGE_WRITE]
        assert scopes.has_scope(granted, scopes.MISSION_CONTROL_RANGE_READ) is True

    def test_missing_scope_is_denied(self):
        assert scopes.has_scope([scopes.MISSION_CONTROL_RANGE_READ], scopes.MISSION_CONTROL_RANGE_WRITE) is False

    def test_no_wildcard_expansion(self):
        # Holding a broad-looking string must not satisfy a specific scope.
        assert scopes.has_scope(["mission_control:*"], scopes.MISSION_CONTROL_RANGE_READ) is False

    def test_empty_grant_denies(self):
        assert scopes.has_scope([], scopes.MISSION_CONTROL_RANGE_READ) is False

    def test_a_retired_stored_risk_scope_is_tolerated_not_raised(self):
        # A persisted ApiToken row minted before #1374 Part B may still carry a
        # retired `risk:read`/`risk:write` string in its stored `scopes` list
        # (nothing rewrites historical rows). Membership-checking that stored
        # value against a still-known required scope must fail closed (the
        # token becomes unusable, which is correct) rather than raise —
        # `has_scope` is a plain set-membership check with no registry lookup,
        # so an unknown stored scope never reaches `KNOWN_SCOPES` at all.
        stored_scopes = ["risk:read"]
        assert scopes.has_scope(stored_scopes, scopes.MISSION_CONTROL_RANGE_READ) is False
