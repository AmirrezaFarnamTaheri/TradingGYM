from decimal import Decimal

import pytest

from trading_gym.portfolio import (
    RebalanceHolding,
    RebalancePolicy,
    RebalanceTarget,
    plan_rebalance,
)


def holdings():
    return [
        RebalanceHolding(
            instrument_id="BTC-USD",
            quantity=Decimal("0.5"),
            price_base=Decimal("100000"),
            quantity_increment=Decimal("0.001"),
            minimum_notional_base=Decimal("10"),
        ),
        RebalanceHolding(
            instrument_id="ETH-USD",
            quantity=Decimal("10"),
            price_base=Decimal("3000"),
            quantity_increment=Decimal("0.01"),
            minimum_notional_base=Decimal("10"),
        ),
    ]


def test_rebalance_sells_before_buys_and_grants_no_authority():
    plan = plan_rebalance(
        base_currency="USD",
        cash_base=Decimal("20000"),
        holdings=holdings(),
        targets=[
            RebalanceTarget(instrument_id="BTC-USD", target_weight=Decimal("0.30")),
            RebalanceTarget(instrument_id="ETH-USD", target_weight=Decimal("0.50")),
        ],
        policy=RebalancePolicy(
            minimum_cash_weight=Decimal("0.10"), maximum_turnover_fraction=Decimal("1")
        ),
    )
    assert plan.feasible is True
    assert plan.trades[0].side.value == "sell"
    assert plan.trades[-1].side.value == "buy"
    assert plan.execution_authority_granted is False
    assert plan.mutation_performed is False


def test_rebalance_is_deterministic_and_respects_increments():
    kwargs = dict(
        base_currency="USD",
        cash_base=Decimal("20000"),
        holdings=holdings(),
        targets=[
            RebalanceTarget(instrument_id="BTC-USD", target_weight=Decimal("0.40")),
            RebalanceTarget(instrument_id="ETH-USD", target_weight=Decimal("0.40")),
        ],
    )
    first = plan_rebalance(**kwargs)
    second = plan_rebalance(**kwargs)
    assert first == second
    for trade in first.trades:
        increment = next(
            item.quantity_increment
            for item in holdings()
            if item.instrument_id == trade.instrument_id
        )
        assert trade.quantity % increment == 0


def test_rebalance_fails_on_turnover_cash_and_short_constraints():
    plan = plan_rebalance(
        base_currency="USD",
        cash_base=Decimal("1"),
        holdings=holdings(),
        targets=[
            RebalanceTarget(instrument_id="BTC-USD", target_weight=Decimal("0")),
            RebalanceTarget(instrument_id="ETH-USD", target_weight=Decimal("0.90")),
        ],
        policy=RebalancePolicy(
            minimum_cash_weight=Decimal("0.05"),
            maximum_turnover_fraction=Decimal("0.01"),
        ),
    )
    assert plan.feasible is False
    assert "maximum_turnover_exceeded" in plan.blockers


def test_invalid_targets_and_missing_metadata_fail_closed():
    with pytest.raises(ValueError):
        plan_rebalance(
            base_currency="USD",
            cash_base=Decimal("100"),
            holdings=holdings(),
            targets=[
                RebalanceTarget(instrument_id="BTC-USD", target_weight=Decimal("0.98"))
            ],
        )
    with pytest.raises(ValueError):
        plan_rebalance(
            base_currency="USD",
            cash_base=Decimal("100"),
            holdings=holdings(),
            targets=[
                RebalanceTarget(instrument_id="SOL-USD", target_weight=Decimal("0.10"))
            ],
        )
