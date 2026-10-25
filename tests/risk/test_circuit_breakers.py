import pytest
import time
from trading_gym.core.config import BreakerConfig
from trading_gym.execution.risk.circuit_breakers import CircuitBreaker, BreakerState


@pytest.fixture
def breaker_config():
    return BreakerConfig(
        max_loss_pct=10.0,
        max_loss_velocity_pct_per_hour=5.0,
        max_single_trade_loss_pct=2.0,
        max_slippage_breach_count=3,
        slippage_breach_window_minutes=10,
        auto_reset=False,
    )


@pytest.fixture
def breaker(breaker_config):
    return CircuitBreaker(breaker_config)


def test_initialization(breaker):
    assert breaker.state == BreakerState.CLOSED
    assert breaker.metrics is not None
    assert len(breaker.rules) > 0


def test_max_loss_rule(breaker):
    # Initial NAV 10000
    # First call establishes initial_nav
    ctx = {"nav": 10000}
    assert not breaker.check(ctx)
    assert breaker.initial_nav == 10000

    # Drop to 9500 (5% loss) - OK
    # Note: If we pass 'nav' only, it calculates loss vs initial (10000).
    # Loss = (10000 - 9500)/10000 = 5%
    # However, loss_velocity check runs on EVERY check().
    # If we feed too many updates in a short time, loss_velocity might trigger if rate is high.
    # But here we only added one point in initialization, and this is the second point.
    # Time delta defaults to now (same second?).
    # Wait, we didn't update time.
    # breaker.check defaults current_time to time.time().
    # If all tests run in < 1ms, dt approx 0.
    # Loss velocity = dLoss / dt. If dt -> 0, velocity -> Infinity.
    # So any loss > 0 will trip loss_velocity if dt is small.

    # FIX: Provide explicit timestamps to avoid infinite velocity
    # If we use "now" for the first point (initial_nav set), and "now + 3600" for the second point.
    # Loss = 5%. Time = 1 hr. Velocity = 5%/hr.
    # Limit is 5%/hr.
    # If velocity > 5.0, it trips.
    # 5.0 > 5.0 is False. So it shouldn't trip.
    # But maybe float precision makes it 5.00000000001?

    # Let's use 2 hours later. Loss = 5% / 2h = 2.5%/hr. Safe.
    # Note: breaker uses `breaker.metrics.nav_history` to calculate velocity.
    # The first call `assert not breaker.check(ctx)` added (now, 10000).
    # We need to make sure that entry is consistent with our new "now".
    # `breaker.check({"nav": 10000})` used `time.time()`.
    # Here we use `now = time.time()` which is slightly later.
    # So history has (t0, 10000).
    # Current check: (t0 + 7200, 9500).
    # Loss velocity = (500/10000) / 2h = 2.5%/hr.
    # Max velocity = 5.0%/hr.
    # Should be OK.

    # Why did it fail? "loss_velocity: Loss velocity exceeds 5.0%/hour"
    # Maybe my history assumption is wrong.
    # Let's manually clear history and seed it to be sure.

    breaker.metrics.nav_history.clear()
    start_time = 1000000.0
    breaker.metrics.nav_history.append((start_time, 10000.0))

    # 1 hour later, 5% loss -> 5%/hr -> OK/Edge
    # 10 hours later, 5% loss -> 0.5%/hr -> VERY Safe
    ctx["current_time"] = start_time + 36000
    ctx["nav"] = 9500.0

    assert not breaker.check(ctx)

    # Drop to 8900 (11% loss) - TRIP
    ctx["nav"] = 8900
    assert breaker.check(ctx)
    assert breaker.state == BreakerState.TRIPPED
    assert "max_loss" in breaker.trip_reason


def test_single_trade_loss_rule(breaker):
    ctx = {"trade_loss_pct": -1.5}
    assert not breaker.check(ctx)

    ctx["trade_loss_pct"] = -2.1
    assert breaker.check(ctx)
    assert breaker.state == BreakerState.TRIPPED
    assert "single_trade_loss" in breaker.trip_reason


def test_loss_velocity_rule(breaker):
    now = time.time()

    # Add history: 10000 at t=0
    # Use 3599 seconds (just under 1 hour) to ensure the entry is within the
    # 60-minute window (max_loss_window_minutes=60 -> 3600 seconds).
    # The window check uses `now - t < window` (strict less than), so entries
    # exactly at the boundary (3600 seconds old) are excluded.
    breaker.metrics.nav_history.append((now - 3599, 10000.0))

    # Check at ~1h with 9600 (4% loss per hour) - OK (<5%)
    ctx = {"current_time": now, "nav": 9600.0}
    assert not breaker.check(ctx)

    # Check at ~1h with 9400 (6% loss per hour) - TRIP (>5%)
    ctx["nav"] = 9400.0
    assert breaker.check(ctx)
    assert breaker.state == BreakerState.TRIPPED
    assert "loss_velocity" in breaker.trip_reason


def test_slippage_breach_rule(breaker):
    now = time.time()

    # Add 3 breaches within window
    breaker.metrics.slippage_breaches.append(now - 100)
    breaker.metrics.slippage_breaches.append(now - 200)
    breaker.metrics.slippage_breaches.append(now - 300)

    ctx = {"current_time": now, "slippage_breach": True}

    # This check calls update_metrics which adds the 4th breach
    assert breaker.check(ctx)
    assert breaker.state == BreakerState.TRIPPED
    assert "slippage_breach" in breaker.trip_reason


def test_manual_reset(breaker):
    ctx = {"trade_loss_pct": -5.0}
    breaker.check(ctx)
    assert breaker.is_tripped()

    breaker.reset(manual=True)
    assert not breaker.is_tripped()
    assert breaker.state == BreakerState.CLOSED


def test_auto_reset_logic(breaker_config):
    breaker_config.auto_reset = True
    breaker_config.cooldown_minutes = 0.001  # very short
    breaker = CircuitBreaker(breaker_config)

    ctx = {"trade_loss_pct": -5.0}
    breaker.check(ctx)
    assert breaker.is_tripped()

    # Wait for cooldown
    time.sleep(0.1)

    # Check again should reset
    # We pass a benign context so it doesn't trip again immediately
    ctx = {"trade_loss_pct": 0.0}
    assert not breaker.check(ctx)
    assert not breaker.is_tripped()


def test_auto_reset_clears_the_window_that_tripped(breaker_config):
    """A cooldown that leaves the tripping burst in the window is pointless.

    With ``auto_reset``, ``check`` falls through to the rules again right
    after the reset; if the burst is still in the window the breaker re-trips
    on the next sample and can never recover. The reset must clear the
    rolling evidence (while keeping the cumulative drawdown and liveness
    anchors, which a cooldown must not forgive).
    """
    breaker_config.auto_reset = True
    breaker_config.cooldown_minutes = 0.001  # very short
    breaker_config.max_api_error_count = 2
    breaker_config.api_error_window_minutes = 60
    breaker = CircuitBreaker(breaker_config)

    now = time.time()
    # A burst of api errors within the window trips api_error_burst.
    for offset in (300, 200, 100):
        breaker.metrics.api_errors.append(now - offset)
    assert breaker.check({"current_time": now, "api_error": True})
    assert "api_error_burst" in breaker.trip_reason

    time.sleep(0.1)
    # The stored burst would still exceed the limit, so a reset that kept it
    # would re-trip here instead of recovering.
    assert not breaker.check({"current_time": time.time()})
    assert not breaker.is_tripped()
    assert breaker.state == BreakerState.CLOSED
    assert len(breaker.metrics.api_errors) == 0


def test_reset_keeps_the_cumulative_drawdown_and_liveness_anchors(breaker):
    """``initial_nav`` and ``last_fill_time`` outlive a reset — they are states,
    not bursts, and forgiving them would let a cooldown hide a real drawdown or
    a stalled market."""
    breaker.initial_nav = 10000.0
    breaker.metrics.last_fill_time = time.time() - 60

    breaker.reset(manual=True)

    assert breaker.initial_nav == 10000.0
    assert breaker.metrics.last_fill_time is not None


def test_custom_rule(breaker):
    def my_rule(ctx):
        return ctx.get("custom_val", 0) > 100

    breaker.register_rule("test_rule", my_rule, "Custom rule")

    ctx = {"custom_val": 50}
    assert not breaker.check(ctx)

    ctx["custom_val"] = 150
    assert breaker.check(ctx)
    assert "test_rule" in breaker.trip_reason


def test_disable_enable(breaker):
    breaker.disable()
    assert breaker.state == BreakerState.DISABLED

    # Should not trip even if condition met
    ctx = {"trade_loss_pct": -50.0}
    assert not breaker.check(ctx)

    breaker.enable()
    assert breaker.state == BreakerState.CLOSED

    # Now it should trip
    assert breaker.check(ctx)


def test_margin_floor_rule_needs_a_supplied_ratio(breaker_config):
    """The margin_floor rule defaults a *missing* margin_ratio to infinity
    (healthy), so a caller that omits it silently disarms the floor. This is
    why the rule stayed inert until the environment began supplying the
    value; a non-finite ratio is rejected rather than treated as safe."""
    safe = CircuitBreaker(breaker_config)
    # Absent key -> treated as healthy, the floor cannot fire.
    assert not safe.check({"nav": 100.0, "initial_nav": 100.0}, current_time=0.0)

    tripped = CircuitBreaker(breaker_config)
    assert tripped.check(
        {"nav": 100.0, "initial_nav": 100.0, "margin_ratio": 0.1},
        current_time=60.0,
    )
    assert tripped.trip_reason.startswith("margin_floor")

    bad = CircuitBreaker(breaker_config)
    assert bad.check(
        {"nav": 100.0, "initial_nav": 100.0, "margin_ratio": float("inf")},
        current_time=120.0,
    )
    assert bad.trip_reason.startswith("invalid_context")
