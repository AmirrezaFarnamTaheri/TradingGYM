from __future__ import annotations

import math

import pytest

from trading_gym.core.config import BreakerConfig
from trading_gym.execution.risk.circuit_breakers import BreakerState, CircuitBreaker


def breaker() -> CircuitBreaker:
    return CircuitBreaker(
        BreakerConfig(
            max_loss_pct=10.0,
            max_loss_velocity_pct_per_hour=5.0,
            max_single_trade_loss_pct=2.0,
            max_slippage_breach_count=3,
            slippage_breach_window_minutes=10,
            auto_reset=False,
        )
    )


@pytest.mark.parametrize(
    "context",
    [
        {"nav": math.nan},
        {"nav": math.inf},
        {"nav": -1.0},
        {"nav": 100.0, "initial_nav": 0.0},
        {"margin_ratio": -0.1},
        {"trade_loss_pct": math.nan},
    ],
)
def test_nonfinite_or_invalid_safety_context_trips(context: dict[str, float]) -> None:
    instance = breaker()
    assert instance.check(context, current_time=100.0) is True
    assert instance.state is BreakerState.TRIPPED
    assert instance.trip_reason and instance.trip_reason.startswith("invalid_context")


def test_rule_exception_trips_instead_of_failing_open() -> None:
    instance = breaker()

    def broken_rule(context):  # type: ignore[no-untyped-def]
        raise RuntimeError("sensitive downstream body")

    instance.register_rule("broken_rule", broken_rule, "must fail closed", priority=100)
    assert instance.check({"nav": 100.0}, current_time=100.0) is True
    assert instance.state is BreakerState.TRIPPED
    assert (
        instance.trip_reason
        == "rule_error.broken_rule: Rule evaluation failed (RuntimeError)"
    )
    assert "sensitive downstream body" not in instance.trip_reason


def test_rule_registration_is_bounded_and_unique() -> None:
    instance = breaker()
    with pytest.raises(ValueError, match="safe identifier"):
        instance.register_rule("../bad", lambda _: False)
    instance.register_rule("custom_safe", lambda _: False)
    with pytest.raises(ValueError, match="already exists"):
        instance.register_rule("custom_safe", lambda _: False)


def test_nonfinite_current_time_trips() -> None:
    instance = breaker()
    assert instance.check({"nav": 100.0}, current_time=math.nan) is True
    assert instance.state is BreakerState.TRIPPED
