"""Unit tests for risk-register serializer score validators (issue #779 burndown)."""

import pytest
from rest_framework import serializers as drf_serializers

from risk_register.api.serializers import (
    RiskCreateSerializer,
    RiskSerializer,
    RiskUpdateSerializer,
)

SERIALIZERS = [RiskSerializer, RiskCreateSerializer, RiskUpdateSerializer]


@pytest.mark.parametrize("cls", SERIALIZERS)
@pytest.mark.parametrize("bad", [0, 6])
def test_likelihood_out_of_range_rejected(cls, bad):
    with pytest.raises(drf_serializers.ValidationError):
        cls().validate_likelihood_score(bad)


@pytest.mark.parametrize("cls", SERIALIZERS)
@pytest.mark.parametrize("bad", [0, 6])
def test_impact_out_of_range_rejected(cls, bad):
    with pytest.raises(drf_serializers.ValidationError):
        cls().validate_impact_score(bad)


@pytest.mark.parametrize("cls", SERIALIZERS)
def test_valid_scores_pass_through(cls):
    assert cls().validate_likelihood_score(3) == 3
    assert cls().validate_impact_score(5) == 5
