from decimal import Decimal

import pytest

from trading_gym.execution.risk.derivatives import (
    DerivativeKind,
    DerivativePosition,
    DerivativeRiskPolicy,
    analyze_derivative_risk,
)


def position(**updates):
    data = dict(
        instrument_id="BTC-PERP",
        kind=DerivativeKind.PERPETUAL,
        quantity=Decimal("0.2"),
        entry_price=Decimal("90000"),
        mark_price=Decimal("100000"),
        contract_multiplier=Decimal("1"),
        initial_margin_rate=Decimal("0.10"),
        maintenance_margin_rate=Decimal("0.05"),
        liquidation_price=Decimal("70000"),
        funding_rate_per_interval=Decimal("0.0001"),
        remaining_funding_intervals=3,
    )
    data.update(updates)
    return DerivativePosition(**data)


def test_derivative_report_is_deterministic_and_non_authoritative():
    report = analyze_derivative_risk(
        equity=Decimal("50000"),
        free_collateral=Decimal("40000"),
        positions=[position()],
        policy=DerivativeRiskPolicy(maximum_position_concentration=Decimal("1")),
    )
    again = analyze_derivative_risk(
        equity=Decimal("50000"),
        free_collateral=Decimal("40000"),
        positions=[position()],
        policy=DerivativeRiskPolicy(maximum_position_concentration=Decimal("1")),
    )
    assert report == again
    assert report.allowed is True
    assert report.execution_authority_granted is False
    assert report.liquidation_authority_granted is False


def test_liquidation_funding_leverage_and_stress_fail_closed():
    report = analyze_derivative_risk(
        equity=Decimal("1000"),
        free_collateral=Decimal("100"),
        positions=[
            position(
                quantity=Decimal("1"),
                liquidation_price=Decimal("95000"),
                funding_rate_per_interval=Decimal("0.02"),
                remaining_funding_intervals=10,
            )
        ],
        policy=DerivativeRiskPolicy(maximum_position_concentration=Decimal("1")),
    )
    assert report.allowed is False
    markers = "\n".join(report.violations)
    for expected in (
        "liquidation_buffer",
        "gross_leverage",
        "funding_drag",
        "stressed_equity_below_maintenance",
    ):
        assert expected in markers


def test_short_position_uses_directional_liquidation_and_funding():
    report = analyze_derivative_risk(
        equity=Decimal("50000"),
        free_collateral=Decimal("40000"),
        positions=[
            position(
                instrument_id="ETH-PERP",
                quantity=Decimal("-3"),
                entry_price=Decimal("4000"),
                mark_price=Decimal("3500"),
                liquidation_price=Decimal("5000"),
            )
        ],
        policy=DerivativeRiskPolicy(maximum_position_concentration=Decimal("1")),
    )
    item = report.position_results[0]
    assert item.unrealized_pnl == Decimal("1500")
    assert item.projected_funding < 0
    assert item.liquidation_buffer_fraction > 0


def test_invalid_margin_and_duplicate_positions_are_rejected():
    with pytest.raises(ValueError):
        position(
            initial_margin_rate=Decimal("0.05"), maintenance_margin_rate=Decimal("0.10")
        )
    with pytest.raises(ValueError):
        analyze_derivative_risk(
            equity=Decimal("1000"),
            free_collateral=Decimal("500"),
            positions=[position(), position()],
        )
