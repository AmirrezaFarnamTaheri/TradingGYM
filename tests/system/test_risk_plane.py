import pytest

from trading_gym.execution.risk.portfolio import (
    PortfolioRiskLimits,
    PortfolioSnapshot,
    PositionExposure,
    evaluate_portfolio_risk,
)
from trading_gym.execution.risk.sizing import PositionSizingRequest, SizingMethod, size_position


def test_position_sizing_applies_caps_rounding_and_minimums() -> None:
    result = size_position(
        PositionSizingRequest(
            method=SizingMethod.RISK_PER_TRADE,
            equity=10_000,
            price=101,
            max_position_fraction=0.1,
            max_leverage=2,
            risk_fraction=0.02,
            stop_distance_fraction=0.01,
            quantity_increment=0.1,
        )
    )
    assert result.notional <= 2_000
    assert abs(result.quantity / 0.1 - round(result.quantity / 0.1)) < 1e-9
    assert "max_position_and_leverage" in result.binding_constraints

    rejected = size_position(
        PositionSizingRequest(
            method=SizingMethod.FIXED_FRACTION,
            equity=100,
            price=100,
            max_position_fraction=0.01,
            min_notional=10,
        )
    )
    assert rejected.rejected_reason == "notional below venue minimum"


def test_portfolio_risk_aggregates_duplicate_instruments_and_gates_losses() -> None:
    snapshot = PortfolioSnapshot(
        equity=80_000,
        peak_equity=100_000,
        day_start_equity=90_000,
        positions=[
            PositionExposure(instrument_id="BTC", notional=15_000, sector="crypto"),
            PositionExposure(instrument_id="BTC", notional=-10_000, sector="crypto"),
            PositionExposure(instrument_id="ETH", notional=20_000, sector="crypto"),
        ],
    )
    decision = evaluate_portfolio_risk(
        snapshot,
        PortfolioRiskLimits(
            max_gross_leverage=0.5,
            max_net_leverage=0.5,
            max_instrument_fraction=0.25,
            max_sector_fraction=0.5,
            max_drawdown_fraction=0.1,
            max_daily_loss_fraction=0.05,
        ),
    )
    assert decision.instrument_fractions["BTC"] == 25_000 / 80_000
    assert decision.allowed is False
    assert {
        "gross_leverage",
        "instrument_concentration",
        "sector_concentration",
        "drawdown",
        "daily_loss",
    }.issubset(decision.violations)


def test_non_finite_notional_is_rejected_not_fail_open() -> None:
    """A NaN notional made gross_leverage NaN, and ``NaN > limit`` is False,
    so every gate silently passed and the decision returned allowed=True."""
    import math

    from pydantic import ValidationError

    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            PositionExposure(instrument_id="BTC", notional=bad)

    # Finite negatives (shorts) must still be accepted.
    assert math.isfinite(
        PositionExposure(instrument_id="BTC", notional=-1_000).notional
    )
