import pytest
from trading_gym.risk.circuit_breakers import CircuitBreaker
from trading_gym.common.config import BreakerConfig


@pytest.fixture
def breaker_config():
    return BreakerConfig(
        max_loss_pct=10.0,
        max_loss_window_minutes=60,
        max_loss_velocity_pct_per_hour=5.0,
        max_single_trade_loss_pct=2.0,
        max_slippage_breach_count=5,
        slippage_breach_window_minutes=10,
        max_api_error_count=10,
        api_error_window_minutes=5,
        max_rejection_rate_pct=20.0,
        margin_ratio_floor=0.4,
        margin_check_enabled=True,
        max_no_fill_minutes=30,
        stall_check_enabled=True,
        auto_reset=False,
        cooldown_minutes=30,
    )


def test_max_loss_breaker(breaker_config):
    breaker = CircuitBreaker(breaker_config)

    # Start with 100 NAV
    # Loss of 10% means NAV <= 90

    # Initial check at t=0
    assert breaker.check({"nav": 100.0, "initial_nav": 100.0}, current_time=0) is False

    # Small loss at t=3600 (1 hour later) to avoid velocity trigger
    # 5% loss in 1 hour = 5%/hr velocity (limit is 5%). OK.
    assert (
        breaker.check({"nav": 95.0, "initial_nav": 100.0}, current_time=3600) is False
    )

    # Breach Max Loss (independent of velocity if absolute loss > 10%)
    assert breaker.check({"nav": 89.0, "initial_nav": 100.0}, current_time=7200) is True
    assert breaker.is_tripped()
    assert "max_loss" in breaker.trip_reason


def test_velocity_breaker(breaker_config):
    breaker = CircuitBreaker(breaker_config)

    # Initial NAV 100
    # Velocity Limit: 5% per hour

    t0 = 0
    breaker.check({"nav": 100.0, "initial_nav": 100.0}, current_time=t0)

    # 30 mins later, loss of 2% (4% per hour rate - OK)
    t1 = t0 + 1800
    assert breaker.check({"nav": 98.0, "initial_nav": 100.0}, current_time=t1) is False

    # 1 min later, drop to 90%
    # Total drop 10% in 31 mins. Rate ~ 20%/hr. Should trip.
    t2 = t1 + 60
    assert breaker.check({"nav": 90.0, "initial_nav": 100.0}, current_time=t2) is True
    assert breaker.is_tripped()
    assert "loss_velocity" in breaker.trip_reason


def test_manual_reset(breaker_config):
    breaker = CircuitBreaker(breaker_config)

    # Trip it
    breaker.check({"nav": 80.0, "initial_nav": 100.0}, current_time=0)
    assert breaker.is_tripped()

    # Manually reset
    breaker.reset()
    assert not breaker.is_tripped()
    assert breaker.state == "closed"

    # Check logic allows new check (pass valid data)
    breaker.check({"nav": 100.0, "initial_nav": 100.0}, current_time=10)
    assert not breaker.is_tripped()
