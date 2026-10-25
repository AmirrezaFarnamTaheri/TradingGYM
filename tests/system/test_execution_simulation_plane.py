from __future__ import annotations

from trading_gym.execution.simulation import (
    ExecutionSimulationPolicy,
    ExecutionSimulationRequest,
    SimulatedOrderStatus,
    simulate_execution,
    simulate_scenarios,
)


def request(**updates):
    payload = dict(
        order_id="order-sim-0001",
        instrument_id="BTC-USD",
        side="buy",
        order_type="market",
        quantity=10.0,
        bid_price=99.0,
        ask_price=101.0,
        interval_volume_units=100.0,
        visible_liquidity_units=100.0,
        volatility_fraction=0.001,
    )
    payload.update(updates)
    return ExecutionSimulationRequest(**payload)


def test_simulation_is_deterministic_and_has_no_authority():
    policy = ExecutionSimulationPolicy(seed=42, max_participation_fraction=0.2)
    first = simulate_execution(request(), policy)
    second = simulate_execution(request(), policy)
    assert first == second
    assert first.execution_price is not None and first.execution_price > 101.0
    assert first.total_cost > 0
    assert not first.execution_authority_granted


def test_simulation_partial_fill_and_minimum_fill_rejection():
    partial = simulate_execution(
        request(quantity=100.0, interval_volume_units=10.0),
        ExecutionSimulationPolicy(max_participation_fraction=0.1),
    )
    assert partial.status is SimulatedOrderStatus.PARTIAL
    assert partial.filled_quantity == 1.0
    rejected = simulate_execution(
        request(quantity=100.0, interval_volume_units=10.0, order_id="order-sim-0002"),
        ExecutionSimulationPolicy(
            max_participation_fraction=0.1, minimum_fill_fraction=0.5
        ),
    )
    assert rejected.status is SimulatedOrderStatus.REJECTED
    assert rejected.filled_quantity == 0.0


def test_non_marketable_limit_is_rejected():
    result = simulate_execution(request(order_type="limit", limit_price=100.0))
    assert result.status is SimulatedOrderStatus.REJECTED
    assert "limit price" in (result.rejection_reason or "")


def test_stress_scenarios_are_at_least_as_conservative():
    scenarios = simulate_scenarios(
        request(quantity=5.0),
        ExecutionSimulationPolicy(seed=7, max_participation_fraction=0.2),
    )
    assert scenarios.adverse.filled_quantity <= scenarios.base.filled_quantity
    assert scenarios.extreme.filled_quantity <= scenarios.adverse.filled_quantity
    if (
        scenarios.extreme.execution_price is not None
        and scenarios.base.execution_price is not None
    ):
        assert scenarios.extreme.execution_price >= scenarios.base.execution_price
