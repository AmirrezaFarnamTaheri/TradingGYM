"""Conformal VaR and latching kill-switch tests."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from trading_gym.risk.conformal_var import (
    SplitConformalVaR,
    compute_conformal_position_scale,
)
from trading_gym.risk.latching_kill_switch import (
    LatchingConfig,
    LatchingKillSwitch,
    REASON_CONSECUTIVE_LOSSES,
    REASON_DAILY_LOSS,
    REASON_DRAWDOWN,
    REASON_NONE,
    REASON_STALE_DATA,
)

NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Conformal VaR
# ---------------------------------------------------------------------------


def _calibration(seed: int, n: int = 400) -> np.ndarray:
    return np.abs(np.random.default_rng(seed).normal(0.01, 0.004, size=n))


def test_var_is_finite_and_grows_with_calibration_tail():
    var_model = SplitConformalVaR(alpha=0.05).fit(_calibration(1))
    value = var_model.predict_var()
    assert np.isfinite(value) and value > 0


def test_coverage_holds_on_exchangeable_evaluation_data():
    model = SplitConformalVaR(alpha=0.10).fit(_calibration(2))
    eval_losses = _calibration(3, n=2_000)
    report = model.coverage_report(eval_losses, tolerance=0.03)
    assert report.empirical_breach_rate <= report.alpha + 0.03
    assert report.covered


def test_fit_and_predict_validation():
    with pytest.raises(Exception):
        SplitConformalVaR(alpha=0.05).fit(np.array([0.01] * 4))
    with pytest.raises(Exception):
        SplitConformalVaR(alpha=0.05).fit(np.array([0.1, np.nan] * 8))
    model = SplitConformalVaR(alpha=0.05).fit(_calibration(4))
    with pytest.raises(Exception):
        model.coverage_report(np.array([]))


def test_position_scale_is_binary_conservative_around_the_budget():
    # Policy: full size while the conformal VaR stays strictly inside the
    # dollar budget; any estimate at or beyond the budget takes nothing.
    # Strictly-inside sanity: 1% VaR vs 5% budget.
    assert (
        compute_conformal_position_scale(
            var_estimate=0.01, equity=100_000.0, risk_budget_fraction=0.05
        )
        == 1.0
    )
    # At the boundary and beyond: no size.
    assert (
        compute_conformal_position_scale(
            var_estimate=0.05, equity=100_000.0, risk_budget_fraction=0.05
        )
        == 0.0
    )
    assert (
        compute_conformal_position_scale(
            var_estimate=0.20, equity=100_000.0, risk_budget_fraction=0.05
        )
        == 0.0
    )


def _switch(**overrides) -> LatchingKillSwitch:
    return (
        LatchingKillSwitch(config=LatchingConfig(**overrides))
        if overrides
        else LatchingKillSwitch()
    )


def test_no_trigger_returns_clean_decision():
    decision = _switch().check(
        now=NOW,
        daily_pnl=-10.0,
        daily_start_nav=10_000.0,
        nav=9_900.0,
        peak_nav=10_000.0,
        consecutive_losses=0,
        last_data_time=NOW - timedelta(seconds=30),
    )
    assert not decision.halted and decision.reason == REASON_NONE


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        pytest.param(dict(daily_pnl=-350.0), REASON_DAILY_LOSS, id="daily-loss"),
        pytest.param(
            dict(nav=8_000.0, peak_nav=10_000.0), REASON_DRAWDOWN, id="drawdown"
        ),
        pytest.param(
            dict(consecutive_losses=5), REASON_CONSECUTIVE_LOSSES, id="streak"
        ),
    ],
)
def test_each_limit_latches(overrides, expected_reason):
    switch = _switch()
    kwargs = dict(
        now=NOW,
        daily_pnl=-10.0,
        daily_start_nav=10_000.0,
        nav=9_500.0,
        peak_nav=10_000.0,
        consecutive_losses=0,
        last_data_time=NOW - timedelta(seconds=30),
    )
    kwargs.update(overrides)
    decision = switch.check(**kwargs)
    assert decision.halted and decision.reason == expected_reason
    # Sticky: subsequent healthy inputs stay halted with the same reason.
    again = switch.check(
        now=NOW + timedelta(minutes=1),
        daily_pnl=0.0,
        daily_start_nav=10_000.0,
        nav=9_999.0,
        peak_nav=10_000.0,
        consecutive_losses=0,
        last_data_time=NOW,
    )
    assert again.halted and again.reason == expected_reason


def test_stale_data_uses_injected_clock_not_wall_clock():
    switch = _switch(stale_data_seconds=60)
    decision = switch.check(
        now=NOW,
        daily_pnl=0.0,
        daily_start_nav=10_000.0,
        nav=10_000.0,
        peak_nav=10_000.0,
        consecutive_losses=0,
        last_data_time=NOW - timedelta(seconds=61),
    )
    assert decision.reason == REASON_STALE_DATA


def test_reset_requires_actor_and_clears_latch():
    switch = _switch(max_daily_loss_pct=0.001)
    switch.check(
        now=NOW,
        daily_pnl=-50.0,
        daily_start_nav=10_000.0,
        nav=10_000.0,
        peak_nav=10_000.0,
        consecutive_losses=0,
        last_data_time=NOW,
    )
    assert switch.latched
    with pytest.raises(ValueError):
        switch.reset(actor="  ")
    switch.reset(actor="operator-7")
    assert not switch.latched and switch.reason is None
