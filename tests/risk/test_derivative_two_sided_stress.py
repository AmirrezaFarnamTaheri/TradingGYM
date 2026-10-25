"""Symmetric adverse market shocks must not hide short-side liquidation risk."""

from decimal import Decimal as D

import pytest

from trading_gym.execution.risk.derivatives import (
    DerivativePosition,
    DerivativeRiskPolicy,
    analyze_derivative_risk,
)


def derivative(instrument, quantity):
    return DerivativePosition(
        instrument_id=instrument,
        kind="perpetual",
        quantity=D(quantity),
        entry_price=D("100"),
        mark_price=D("100"),
        initial_margin_rate=D("0.10"),
        maintenance_margin_rate=D("0.05"),
    )


def policy():
    return DerivativeRiskPolicy(
        maximum_gross_leverage=D("20"),
        maximum_position_concentration=D("1"),
        minimum_margin_ratio=D("0.5"),
        stress_price_shock_fraction=D("-0.15"),
    )


@pytest.mark.parametrize(
    "signed_quantity",
    ["1", "-1"],
)
def test_short_and_long_each_receive_adverse_15_percent_stress(signed_quantity):
    report = analyze_derivative_risk(
        equity=D("18"),
        free_collateral=D("10"),
        positions=[derivative("BTC-PERP", signed_quantity)],
        policy=policy(),
    )
    assert report.position_results[0].stressed_pnl == D("-15")
    assert report.stressed_equity == D("3")
    assert "stressed_equity_below_maintenance" in report.violations
    assert report.allowed is False
    assert report.execution_authority_granted is False


def test_hedged_book_uses_consistent_portfolio_wide_stress_direction():
    report = analyze_derivative_risk(
        equity=D("30"),
        free_collateral=D("10"),
        positions=[
            derivative("BTC-LONG", "1"),
            derivative("BTC-SHORT", "-1"),
        ],
        policy=policy(),
    )
    assert report.stressed_equity == D("30")
    assert sum((item.stressed_pnl for item in report.position_results), D("0")) == D(
        "0"
    )
    assert report.allowed is True


def test_net_long_book_selects_down_shock_and_attributed_leg_pnl():
    report = analyze_derivative_risk(
        equity=D("40"),
        free_collateral=D("15"),
        positions=[
            derivative("BTC-LONG", "2"),
            derivative("BTC-SHORT", "-1"),
        ],
        policy=policy(),
    )
    legs = {item.instrument_id: item for item in report.position_results}
    assert legs["BTC-LONG"].stressed_pnl == D("-30")
    assert legs["BTC-SHORT"].stressed_pnl == D("15")
    assert report.stressed_equity == D("25")


def test_zero_shock_leaves_book_unstressed():
    safe = policy().model_copy(update={"stress_price_shock_fraction": D("0")})
    report = analyze_derivative_risk(
        equity=D("50"),
        free_collateral=D("25"),
        positions=[derivative("BTC-SHORT", "-1")],
        policy=safe,
    )
    assert report.stressed_equity == D("50")
