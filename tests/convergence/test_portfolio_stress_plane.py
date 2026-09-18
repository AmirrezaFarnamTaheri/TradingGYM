import pytest

from trading_gym.risk.stress import (
    PortfolioStressPolicy,
    StressPosition,
    StressScenario,
    evaluate_portfolio_stress,
)


def test_multi_asset_stress_handles_long_short_fx_and_costs_deterministically():
    positions = [
        StressPosition(
            instrument_id="BTC-USDT",
            asset_class="crypto",
            currency="USD",
            notional_base=1000,
        ),
        StressPosition(
            instrument_id="SPY",
            asset_class="equity",
            currency="USD",
            notional_base=-500,
        ),
        StressPosition(
            instrument_id="EWG", asset_class="equity", currency="EUR", notional_base=400
        ),
    ]
    scenario = StressScenario(
        scenario_id="stress:macro-0001",
        label="macro selloff",
        asset_class_shocks={"crypto": -0.30, "equity": -0.10},
        currency_shocks={"EUR": -0.05},
        liquidity_cost_fraction=0.01,
    )
    first = evaluate_portfolio_stress(
        equity=2000, positions=positions, scenario=scenario
    )
    second = evaluate_portfolio_stress(
        equity=2000, positions=positions, scenario=scenario
    )
    assert first == second
    assert first.total_pnl_base < 0
    assert first.execution_authority_granted is False
    assert first.mutation_performed is False


def test_stress_policy_blocks_loss_and_leverage():
    report = evaluate_portfolio_stress(
        equity=100,
        positions=[
            StressPosition(
                instrument_id="BTC-USDT",
                asset_class="crypto",
                currency="USD",
                notional_base=500,
            )
        ],
        scenario=StressScenario(
            scenario_id="stress:crash-0001",
            label="crash",
            default_price_shock_fraction=-0.5,
        ),
        policy=PortfolioStressPolicy(
            max_loss_fraction=0.1, max_post_stress_gross_leverage=1.0
        ),
    )
    assert report.allowed is False
    assert "loss_fraction" in report.violations


def test_stress_rejects_nonfinite_values():
    with pytest.raises(ValueError, match="finite"):
        StressPosition(instrument_id="BTC", currency="USD", notional_base=float("nan"))
