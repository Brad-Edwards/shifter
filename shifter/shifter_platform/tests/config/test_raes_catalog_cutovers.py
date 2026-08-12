"""Tests for the RAES catalog source-route selector parsing (#1310).

Exercises the strict grammar, immutability, and fail-closed two-key posture of
``config._raes_settings._parse_catalog_cutovers`` / ``_parse_strict_bool`` -- the
selector that owns the ADR-024 default cutover (ADR-031-R6). Resolvability of a
route target against a registered, conformance-passed source is enforced later
at registry resolution, not here.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from config._raes_settings import _parse_catalog_cutovers, _parse_strict_bool


class TestParseStrictBool:
    def test_true_tokens(self):
        for raw in ("true", "TRUE", " True ", "1", "yes", "on"):
            assert _parse_strict_bool(raw, name="X") is True

    def test_false_tokens(self):
        for raw in ("false", "FALSE", "0", "no", "off", ""):
            assert _parse_strict_bool(raw, name="X") is False

    def test_unrecognized_raises(self):
        with pytest.raises(ImproperlyConfigured):
            _parse_strict_bool("maybe", name="SHIFTER_RAES_NATIVE_PROVISIONING")


class TestParseCatalogCutovers:
    def test_empty_is_rollback_posture(self):
        assert dict(_parse_catalog_cutovers("", native_enabled=False)) == {}
        assert dict(_parse_catalog_cutovers("   ", native_enabled=True)) == {}

    def test_single_route(self):
        result = _parse_catalog_cutovers("polaris=polaris-raes", native_enabled=True)
        assert dict(result) == {"polaris": "polaris-raes"}

    def test_multiple_routes_with_whitespace(self):
        result = _parse_catalog_cutovers("polaris=polaris-raes, boreas=boreas-raes", native_enabled=True)
        assert dict(result) == {"polaris": "polaris-raes", "boreas": "boreas-raes"}

    def test_result_is_immutable(self):
        result = _parse_catalog_cutovers("polaris=polaris-raes", native_enabled=True)
        with pytest.raises(TypeError):
            result["x"] = "y"  # type: ignore[index]

    def test_non_empty_requires_native_capability(self):
        with pytest.raises(ImproperlyConfigured):
            _parse_catalog_cutovers("polaris=polaris-raes", native_enabled=False)

    @pytest.mark.parametrize(
        "raw",
        [
            "polaris",  # no '='
            "polaris==polaris-raes",  # two '='
            "polaris=",  # empty source
            "=polaris-raes",  # empty public
            "polaris=polaris",  # source not distinct from public
            "polaris=polaris-raes,polaris=other",  # duplicate public id
            "polaris=shared,boreas=shared",  # duplicate source id
            "pol aris=polaris-raes",  # invalid slug (space)
            "polaris=" + "a" * 101,  # source exceeds length bound
            "polaris=polaris-raes,",  # empty trailing segment (no last-wins/ignore)
        ],
    )
    def test_malformed_raises(self, raw):
        with pytest.raises(ImproperlyConfigured):
            _parse_catalog_cutovers(raw, native_enabled=True)
