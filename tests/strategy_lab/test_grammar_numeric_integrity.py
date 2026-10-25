"""Reject implicit boolean coercion and nonfinite strategy grammar inputs."""

import pytest

from trading_gym.agent.strategy_lab.models import IndicatorSpec, Operand, RiskSpec


@pytest.mark.parametrize(
    "raw", [True, False, float("nan"), float("inf"), -float("inf")]
)
def test_numeric_constant_rejects_nonfinite_or_boolean_values(raw):
    with pytest.raises(ValueError):
        Operand(kind="constant", value=raw)


@pytest.mark.parametrize(
    "field",
    [
        "max_position_fraction",
        "max_leverage",
        "risk_per_trade_fraction",
        "target_volatility",
        "stop_loss_fraction",
        "take_profit_fraction",
        "max_portfolio_drawdown_fraction",
        "max_daily_loss_fraction",
        "max_sector_fraction",
        "max_holding_bars",
    ],
)
def test_risk_values_cannot_silently_coerce_true_to_numeric_limit(field):
    with pytest.raises(ValueError):
        RiskSpec(**{field: True})


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_indicator_period_is_rejected_as_validation_error(raw):
    with pytest.raises(ValueError):
        IndicatorSpec(name="ema", alias="fast_ema", params={"period": raw})


def test_legitimate_zero_and_finite_constant_preserved():
    assert Operand(kind="constant", value=0).value == 0.0
    assert Operand(kind="constant", value=25.0).value == 25.0
    assert RiskSpec(max_position_fraction=0.05).max_position_fraction == 0.05
