"""Tail-risk estimates must reject missing data and state statistical limits."""

import math

import pytest

from trading_gym.risk.analytics import tail_risk


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), -float("inf"), True, False, "0.0"]
)
def test_malformed_observations_cannot_claim_zero_risk(bad):
    with pytest.raises(ValueError, match="finite|numeric"):
        tail_risk([0.03, bad, -0.01])


@pytest.mark.parametrize("bad", [True, "0.99", float("nan"), float("inf"), 1.0, 0.5])
def test_confidence_is_strict_finite_probability(bad):
    with pytest.raises(ValueError, match="confidence"):
        tail_risk([-0.01, 0.01], confidence=bad)


def test_empirical_var_es_use_partial_tail_observation_without_implicit_fill():
    result = tail_risk(
        [-0.04, -0.02, 0.0, 0.01, 0.02],
        confidence=0.70,
        method="historical",
        provenance="sealed-fixture",
    )
    assert result["var"] == pytest.approx(0.02)
    assert result["expected_shortfall"] == pytest.approx((0.04 + 0.5 * 0.02) / 1.5)
    assert result["historical_expected_shortfall"] == pytest.approx(
        result["expected_shortfall"]
    )
    assert result["effective_tail_observations"] == pytest.approx(1.5)
    assert result["provenance"] == "sealed-fixture"
    assert result["advisory_only"] is True
    assert result["authority_granted"] is False


def test_gaussian_es_exceeds_var_and_is_finite():
    result = tail_risk([-0.04, -0.02, 0.0, 0.02, 0.04], method="gaussian")
    assert math.isfinite(result["var"])
    assert math.isfinite(result["expected_shortfall"])
    assert result["expected_shortfall"] >= result["var"] >= 0


def test_cornish_fisher_does_not_fabricate_parametric_es():
    values = [-0.04, -0.02, 0.0, 0.02, 0.04] * 10
    result = tail_risk(values, method="cornish-fisher")
    assert result["expected_shortfall"] is None
    assert math.isfinite(result["historical_expected_shortfall"])
    assert any("cornish_fisher" in warning for warning in result["warnings"])


def test_flat_market_remains_zero_after_warmup():
    for method in ("gaussian", "historical", "cornish-fisher"):
        result = tail_risk([0.0] * 30, method=method)
        assert result["var"] == 0
        assert result["historical_expected_shortfall"] == 0
        if result["expected_shortfall"] is not None:
            assert result["expected_shortfall"] == 0


def test_small_sample_is_flagged_not_silently_assumed_reliable():
    result = tail_risk([-0.1, 0.2], confidence=0.99)
    assert "insufficient_tail_observations" in result["warnings"]
    assert result["observations"] == 2


def test_invalid_method_and_provenance_are_denied():
    with pytest.raises(ValueError, match="method"):
        tail_risk([0.0, 0.1], method="magic")
    with pytest.raises(ValueError, match="provenance"):
        tail_risk([0.0, 0.1], provenance=" ")


def test_infinite_stream_is_bounded_before_materialization():
    observed = [0]

    def stream():
        while True:
            observed[0] += 1
            yield 0.01

    with pytest.raises(ValueError, match="budget"):
        tail_risk(stream())
    assert observed[0] <= 100_001
