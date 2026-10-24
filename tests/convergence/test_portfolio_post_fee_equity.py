"""Projected concentration must include equity lost to estimated trading costs."""

from decimal import Decimal

import pytest

from trading_gym.portfolio.rebalance import (
    RebalanceHolding,
    RebalancePolicy,
    RebalanceTarget,
    plan_rebalance,
)


def _holding(quantity="0"):
    return RebalanceHolding(
        instrument_id="ABC-USD",
        quantity=Decimal(quantity),
        price_base=Decimal("1"),
        quantity_increment=Decimal("1"),
    )


def test_post_fee_concentration_breach_blocks_rebalance():
    plan = plan_rebalance(
        base_currency="USD",
        cash_base=Decimal("100"),
        holdings=[_holding()],
        targets=[
            RebalanceTarget(
                instrument_id="ABC-USD",
                target_weight=Decimal("0.9"),
                maximum_weight=Decimal("0.95"),
            )
        ],
        policy=RebalancePolicy(
            minimum_cash_weight=Decimal("0"),
            maximum_turnover_fraction=Decimal("1"),
            estimated_cost_bps=Decimal("1000"),
        ),
    )
    assert plan.equity_base == 100
    assert plan.projected_cash_base == 1
    assert plan.projected_cash_weight == Decimal("1") / Decimal("91")
    assert plan.trades[0].projected_weight == Decimal("90") / Decimal("91")
    assert "maximum_weight:ABC-USD" in plan.blockers
    assert plan.feasible is False
    assert plan.execution_authority_granted is False


def test_no_fee_case_preserves_target_weights():
    plan = plan_rebalance(
        base_currency="USD",
        cash_base=Decimal("100"),
        holdings=[_holding()],
        targets=[
            RebalanceTarget(
                instrument_id="ABC-USD",
                target_weight=Decimal("0.9"),
                maximum_weight=Decimal("0.95"),
            )
        ],
        policy=RebalancePolicy(
            minimum_cash_weight=Decimal("0"),
            maximum_turnover_fraction=Decimal("1"),
            estimated_cost_bps=Decimal("0"),
        ),
    )
    assert plan.feasible is True
    assert plan.projected_cash_weight == Decimal("0.1")
    assert plan.trades[0].projected_weight == Decimal("0.9")


def test_costs_exhausting_projected_equity_are_rejected():
    with pytest.raises(ValueError, match="projected_equity"):
        plan_rebalance(
            base_currency="USD",
            cash_base=Decimal("0"),
            holdings=[
                RebalanceHolding(
                    instrument_id="ABC-USD",
                    quantity=Decimal("100"),
                    price_base=Decimal("1"),
                    quantity_increment=Decimal("1"),
                )
            ],
            targets=[
                RebalanceTarget(
                    instrument_id="ABC-USD",
                    target_weight=Decimal("0"),
                )
            ],
            policy=RebalancePolicy(
                minimum_cash_weight=Decimal("0"),
                maximum_turnover_fraction=Decimal("2"),
                estimated_cost_bps=Decimal("10000"),
            ),
        )


def test_multileg_post_fee_cash_and_assets_reconcile_to_one():
    holdings = [
        RebalanceHolding(
            instrument_id="ABC-USD",
            quantity=Decimal("25"),
            price_base=Decimal("2"),
            quantity_increment=Decimal("1"),
        ),
        RebalanceHolding(
            instrument_id="XYZ-USD",
            quantity=Decimal("10"),
            price_base=Decimal("3"),
            quantity_increment=Decimal("1"),
        ),
    ]
    targets = [
        RebalanceTarget(instrument_id="ABC-USD", target_weight=Decimal("0.3")),
        RebalanceTarget(instrument_id="XYZ-USD", target_weight=Decimal("0.4")),
    ]
    plan = plan_rebalance(
        base_currency="USD",
        cash_base=Decimal("20"),
        holdings=holdings,
        targets=targets,
        policy=RebalancePolicy(
            minimum_cash_weight=Decimal("0.1"),
            maximum_turnover_fraction=Decimal("2"),
            estimated_cost_bps=Decimal("500"),
        ),
    )
    projected_weight_sum = (
        plan.projected_cash_weight
        + sum(plan.residual_weights.values(), Decimal("0"))
        + sum((target.target_weight for target in targets), Decimal("0"))
    )
    assert abs(projected_weight_sum - 1) < Decimal("1e-22")
    assert plan.execution_authority_granted is False
