"""Validation-stats absorptions: quintile spread, window score, random baseline,
fill reconciliation, token bucket."""

import numpy as np
import pytest

from trading_gym.common.operational_utils import (
    TokenBucketLimiter,
    reconcile_fill,
)
from trading_gym.research.validation_stats import (
    composite_window_score,
    fragility_penalty,
    quintile_spread_ttest,
    random_entry_baseline,
)

# ---------------------------------------------------------------------------
# Quintile spread
# ---------------------------------------------------------------------------


def test_quintile_spread_detects_real_ranking_skill():
    rng = np.random.default_rng(3)
    periods, inst = 200, 20
    signals = rng.normal(size=(periods, inst))
    # True alpha: forward return = 0.01 × signal + noise.
    rets = 0.01 * signals + rng.normal(0, 0.002, size=(periods, inst))
    res = quintile_spread_ttest(signals, rets)
    assert res.mean_spread > 0
    assert res.p_value < 0.001


def test_quintile_spread_noise_is_insignificant():
    rng = np.random.default_rng(4)
    sig = rng.normal(size=(100, 10))
    rets = rng.normal(0, 0.01, size=(100, 10))
    res = quintile_spread_ttest(sig, rets)
    assert res.p_value > 0.01


def test_quintile_spread_validates_inputs():
    with pytest.raises(ValueError):
        quintile_spread_ttest(np.ones((5, 3)), np.ones((5, 3)), quantiles=10)
    with pytest.raises(ValueError):
        quintile_spread_ttest(np.array([[1.0]]), np.array([[1.0]]))


# ---------------------------------------------------------------------------
# Window score / fragility
# ---------------------------------------------------------------------------


def test_fragility_penalty_grows_with_negative_slices_and_instability():
    calm_positive = np.array([0.01, 0.02, 0.015, 0.01])
    choppy_negative = np.array([-0.05, 0.08, -0.04, 0.06])
    assert fragility_penalty(choppy_negative) > fragility_penalty(calm_positive)
    with pytest.raises(ValueError):
        fragility_penalty(np.array([]))


def test_composite_score_penalises_fragility():
    base = dict(
        relative_return=0.2, sharpe=1.5, max_drawdown=-0.10, turnover=2.0, win_rate=0.6
    )
    stable = composite_window_score(oos_slice_returns=np.array([0.02] * 8), **base)
    fragile = composite_window_score(
        oos_slice_returns=np.array([0.09, -0.06] * 4), **base
    )
    assert fragile < stable
    assert breakthrough_ok(
        base["relative_return"], base["sharpe"], base["max_drawdown"], base["turnover"]
    )


def breakthrough_ok(rel, sharpe, mdd, to):
    from trading_gym.research.validation_stats import breakthrough_status

    return breakthrough_status(
        relative_return=rel, sharpe=sharpe, max_drawdown=mdd, turnover=to
    )


# ---------------------------------------------------------------------------
# Random-entry baseline percentile gate (nepse absorption)
# ---------------------------------------------------------------------------


def test_random_baseline_gate_separates_edge_from_luck():
    rng = np.random.default_rng(6)
    edge_returns = rng.normal(0.0008, 0.004, size=400)  # real drift
    lucky_returns = rng.normal(0.0000, 0.010, size=400)  # pure luck spike

    def sharpe(x):
        s = x.std(ddof=1)
        return float(x.mean() / s)

    edge_res = random_entry_baseline(
        edge_returns, sharpe(edge_returns), n_simulations=300
    )
    assert edge_res.passes_95

    lucky_sharpe = sharpe(lucky_returns)
    lucky_res = random_entry_baseline(lucky_returns, lucky_sharpe, n_simulations=300)
    assert lucky_res.percentile < 95.0


def test_random_baseline_validation():
    with pytest.raises(ValueError):
        random_entry_baseline(np.array([0.01]), 1.0)


# ---------------------------------------------------------------------------
# Fill reconciliation tolerance (QuantDinger absorption)
# ---------------------------------------------------------------------------


def test_reconcile_fill_tolerance_band():
    ok = reconcile_fill(
        expected_quantity=1.0, filled_quantity=1.0009, tolerance_fraction=0.001
    )
    assert ok.matched and abs(ok.delta) <= 0.001 * 1.0
    bad = reconcile_fill(
        expected_quantity=1.0, filled_quantity=1.5, tolerance_fraction=0.001
    )
    assert not bad.matched and bad.delta == pytest.approx(0.5)
    with pytest.raises(ValueError):
        reconcile_fill(expected_quantity=float("nan"), filled_quantity=1.0)


# ---------------------------------------------------------------------------
# Token bucket with Retry-After preference (longbridge absorption)
# ---------------------------------------------------------------------------


def test_token_bucket_honors_retry_after_over_natural_refill():
    bucket = TokenBucketLimiter(rate_per_second=1_000.0, burst=1)
    bucket.acquire()  # drains the burst token; refill alone would be ~instant
    bucket.penalize(0.05)
    waited = bucket.acquire(sleep=lambda s: None)  # no real sleeping
    # The penalty dominates the natural refill window for this rate.
    assert waited >= 0.05 - 1e-6


def test_token_bucket_burst_capacity():
    bucket = TokenBucketLimiter(rate_per_second=1.0, burst=3)
    sleeps = [bucket.acquire(sleep=lambda s: None) for _ in range(3)]
    assert all(s == 0.0 for s in sleeps)  # full burst consumes instantly
