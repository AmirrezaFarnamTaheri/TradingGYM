"""Pins that breaker status does not advertise inert rules as enabled.

Five default rules read context keys that no production caller supplies
(trade_loss_pct, slippage_breach, api_error, order_attempt/order_rejected,
fill).  Before this fix, ``get_status()["enabled_rules"]`` listed them all as
enabled, so ``max_no_fill_minutes`` in particular gave false assurance.
Status now reports them separately as ``unwired_rules``.
"""

from __future__ import annotations

from trading_gym.risk.circuit_breakers import BreakerConfig, CircuitBreaker

_INERT = {
    "single_trade_loss",
    "slippage_breach",
    "api_error_burst",
    "rejection_rate",
    "stall",
}


def test_inert_rules_are_reported_as_unwired_not_enabled() -> None:
    breaker = CircuitBreaker(BreakerConfig())
    status = breaker.get_status()

    # They are still switched on (the config is honoured)...
    assert _INERT <= set(status["enabled_rules"])
    # ...but status says plainly that nothing feeds them.
    assert set(status["unwired_rules"]) == _INERT


def test_a_rule_becomes_wired_once_its_key_is_supplied() -> None:
    breaker = CircuitBreaker(BreakerConfig())
    breaker.check({"nav": 100.0, "initial_nav": 100.0, "fill": 1.0}, current_time=0.0)
    status = breaker.get_status()

    assert "stall" not in status["unwired_rules"]
    # The other four still have no producer, so they stay flagged.
    assert {
        "single_trade_loss",
        "slippage_breach",
        "api_error_burst",
        "rejection_rate",
    } <= set(status["unwired_rules"])


def test_unwired_reporting_is_not_a_trip_or_a_failure() -> None:
    breaker = CircuitBreaker(BreakerConfig())
    status = breaker.get_status()
    assert status["state"] != "tripped"
    assert breaker.is_tripped() is False


def test_metrics_summary_uses_the_same_clock_as_the_events():
    """Rolling windows must be filtered against the clock that stamped them.

    The RL env passes simulation time as ``current_time``; filtering the
    summary with wall clock reported zero recent events and a 29-million-minute
    stall right after two fills.
    """
    breaker = CircuitBreaker(config=BreakerConfig())
    for sim_time in (1000.0, 1010.0):
        breaker.check(
            {
                "nav": 9000.0,
                "initial_nav": 10000.0,
                "api_error": True,
                "slippage_breach": True,
                "fill": True,
            },
            current_time=sim_time,
        )
    summary = breaker.get_status()["metrics"]
    assert summary["recent_api_errors"] == 2
    assert summary["recent_slippage_breaches"] == 2
    assert summary["time_since_last_fill"] == 0.0
