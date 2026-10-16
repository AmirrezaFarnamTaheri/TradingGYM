"""Non-finite portfolio values must not silently bypass leverage/loss gates."""

import math

import pytest
from pydantic import ValidationError

from trading_gym.risk.portfolio import (
    PortfolioRiskLimits,
    PortfolioSnapshot,
    PositionExposure,
    evaluate_portfolio_risk,
)


@pytest.mark.parametrize("field", ["equity", "peak_equity", "day_start_equity"])
@pytest.mark.parametrize("bad", [float("inf"), 1e308, True, 1e-300])
def test_unbounded_or_false_numeric_account_fields_denied(field, bad):
    values = dict(equity=1_000.0, peak_equity=1_500.0, day_start_equity=1_100.0)
    values[field] = bad
    with pytest.raises(ValidationError):
        PortfolioSnapshot(**values)


@pytest.mark.parametrize("bad", [float("inf"), 1e308, True, False])
def test_invalid_position_notional_denied(bad):
    with pytest.raises(ValidationError):
        PositionExposure(instrument_id="BTC", notional=bad)


@pytest.mark.parametrize("field", [
    "max_gross_leverage", "max_net_leverage", "max_instrument_fraction",
    "max_sector_fraction", "max_drawdown_fraction", "max_daily_loss_fraction",
])
def test_boolean_limits_denied_instead_of_coerced_to_one(field):
    with pytest.raises(ValidationError):
        PortfolioRiskLimits(**{field: True})


def test_valid_short_and_conservative_loss_gates_keep_finite_json_results():
    snapshot = PortfolioSnapshot(
        equity=80_000.0, peak_equity=100_000.0, day_start_equity=90_000.0,
        positions=[
            PositionExposure(instrument_id="BTC", notional=15_000, sector="crypto"),
            PositionExposure(instrument_id="BTC", notional=-10_000, sector="crypto"),
            PositionExposure(instrument_id="ETH", notional=20_000, sector="crypto"),
        ],
    )
    decision = evaluate_portfolio_risk(snapshot, PortfolioRiskLimits(
        max_gross_leverage=0.5, max_drawdown_fraction=0.1,
        max_daily_loss_fraction=0.05, max_instrument_fraction=0.25,
        max_sector_fraction=0.5,
    ))
    assert decision.allowed is False
    assert "gross_leverage" in decision.violations
    assert "drawdown" in decision.violations
    assert "daily_loss" in decision.violations
    assert decision.instrument_fractions["BTC"] == pytest.approx(25_000 / 80_000)
    assert all(math.isfinite(v) for v in (
        decision.gross_leverage, decision.net_leverage,
        decision.drawdown_fraction, decision.daily_loss_fraction,
    ))
    assert "Infinity" not in decision.model_dump_json()
