"""Regime-weighted conformal VaR tests."""

import numpy as np
import pytest

from trading_gym.risk.conformal_var import (
    ConformalVaRError,
    RegimeWeightedConformalVaR,
    SplitConformalVaR,
    compute_conformal_position_scale,
)


def _regime_calibration(seed: int = 7, n_per: int = 300):
    rng = np.random.default_rng(seed)
    # Calm regime: small losses. Crisis regime: heavy tail.
    calm = np.abs(rng.normal(0.008, 0.002, size=n_per))
    crisis = np.abs(rng.normal(0.02, 0.008, size=n_per))
    losses = np.concatenate([calm, crisis])
    labels = np.asarray(["calm"] * n_per + ["crisis"] * n_per)
    return losses, labels


def test_per_regime_quantile_matches_standalone_fit():
    losses, labels = _regime_calibration()
    model = RegimeWeightedConformalVaR(alpha=0.05).fit(losses, labels)

    calm_mask = labels == "calm"
    standalone = SplitConformalVaR(alpha=0.05).fit(losses[calm_mask])
    assert model.predict_var("calm") == pytest.approx(standalone.predict_var())


def test_crisis_tail_exceeds_calm_tail():
    losses, labels = _regime_calibration()
    model = RegimeWeightedConformalVaR(alpha=0.05).fit(losses, labels)
    assert model.predict_var("crisis") > model.predict_var("calm")


def test_thin_regime_falls_back_to_pooled():
    losses, labels = _regime_calibration()
    thin_losses = np.append(losses, [0.031, 0.032])
    thin_labels = np.append(labels, ["thin", "thin"])
    model = RegimeWeightedConformalVaR(alpha=0.05, min_regime_samples=30).fit(
        thin_losses, thin_labels
    )
    pooled = SplitConformalVaR(alpha=0.05).fit(thin_losses)
    assert model.regime_models()["thin"] == "pooled"
    assert model.regime_models()["calm"] == "regime"
    assert model.predict_var("thin") == pytest.approx(pooled.predict_var())
    assert model.sample_counts()["thin"] == 2


def test_probability_blending_is_convex_and_normalized():
    losses, labels = _regime_calibration()
    model = RegimeWeightedConformalVaR(alpha=0.05).fit(losses, labels)
    var_calm = model.predict_var("calm")
    var_crisis = model.predict_var("crisis")

    blended = model.predict_var("calm", {"calm": 0.5, "crisis": 0.5})
    assert blended == pytest.approx((var_calm + var_crisis) / 2.0)

    # Unnormalised weights are normalised internally.
    rescaled = model.predict_var("calm", {"calm": 25.0, "crisis": 25.0})
    assert rescaled == pytest.approx(blended)

    skewed = model.predict_var("calm", {"calm": 0.9, "crisis": 0.1})
    assert var_calm < skewed < var_crisis


def test_input_validation_failures():
    losses, labels = _regime_calibration(n_per=40)
    model = RegimeWeightedConformalVaR(alpha=0.05).fit(losses, labels)

    with pytest.raises(ConformalVaRError):
        model.predict_var("nonexistent")
    with pytest.raises(ConformalVaRError):
        model.predict_var("calm", {"calm": 0.5, "ghost": 0.5})
    with pytest.raises(ConformalVaRError):
        model.predict_var("calm", {"calm": -0.5})
    with pytest.raises(ConformalVaRError):
        model.predict_var("calm", {})
    with pytest.raises(ConformalVaRError):
        RegimeWeightedConformalVaR().predict_var("calm")
    with pytest.raises(ValueError):
        RegimeWeightedConformalVaR(min_regime_samples=4)
    with pytest.raises(ConformalVaRError):
        RegimeWeightedConformalVaR().fit(losses[:-1], labels)
    with pytest.raises(ValueError):
        RegimeWeightedConformalVaR(alpha=1.5)


def test_coverage_on_exchangeable_evaluation_data():
    losses, labels = _regime_calibration(seed=11)
    model = RegimeWeightedConformalVaR(alpha=0.10).fit(losses, labels)
    rng = np.random.default_rng(23)
    eval_calm = np.abs(rng.normal(0.008, 0.002, size=400))
    eval_crisis = np.abs(rng.normal(0.02, 0.008, size=200))
    eval_losses = np.concatenate([eval_calm, eval_crisis])
    eval_labels = np.asarray(["calm"] * 400 + ["crisis"] * 200)

    report = model.coverage_report(eval_losses, eval_labels)
    assert report.covered
    assert set(report.per_regime) == {"calm", "crisis"}
    assert report.per_regime["crisis"].empirical_breach_rate <= 0.10 + 0.02

    with pytest.raises(ConformalVaRError):
        model.coverage_report(eval_losses, np.append(eval_labels, "calm"))
    with pytest.raises(ConformalVaRError):
        model.coverage_report(np.array([]), np.asarray([]))


def test_position_scale_mapping_consumes_blended_estimate():
    losses, labels = _regime_calibration()
    model = RegimeWeightedConformalVaR(alpha=0.05).fit(losses, labels)
    var = model.predict_var("crisis", {"calm": 0.25, "crisis": 0.75})
    equity = 100_000.0
    budget = 0.02
    scale = compute_conformal_position_scale(
        var_estimate=var,
        equity=equity,
        risk_budget_fraction=budget,
    )
    assert 0.0 <= scale <= 1.0
    # The dollar VaR at full size must not exceed the risk budget when scaled.
    assert scale * var * equity <= budget * equity + 1e-9
