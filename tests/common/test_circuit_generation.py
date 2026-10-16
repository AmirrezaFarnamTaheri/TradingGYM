"""Deterministic concurrency regressions for provider circuit recovery."""

import threading

import pytest

from trading_gym.common.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


def _launch(operation, results):
    def worker():
        try:
            results.append(("ok", operation()))
        except BaseException as exc:
            results.append(("error", exc))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _boom():
    raise RuntimeError("provider failed")


def test_stale_success_does_not_close_newly_open_circuit():
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=30)
    entered, release, results = threading.Event(), threading.Event(), []

    def slow_success():
        entered.set()
        assert release.wait(2)
        return "old-result"

    pending = _launch(lambda: breaker.call(slow_success), results)
    assert entered.wait(2)
    with pytest.raises(RuntimeError, match="provider failed"):
        breaker.call(_boom)
    assert breaker.snapshot().state is CircuitState.OPEN
    release.set()
    pending.join(2)
    assert not pending.is_alive()
    assert results == [("ok", "old-result")]
    assert breaker.snapshot().state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "new")


def test_old_failure_cannot_reopen_after_a_successful_recovery(monkeypatch):
    import trading_gym.common.circuit_breaker as source

    clock = [10.0]
    monkeypatch.setattr(source.time, "monotonic", lambda: clock[0])
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=5)
    entered, release, results = threading.Event(), threading.Event(), []

    def slow_failure():
        entered.set()
        assert release.wait(2)
        raise ValueError("old request failed")

    pending = _launch(lambda: breaker.call(slow_failure), results)
    assert entered.wait(2)
    with pytest.raises(RuntimeError):
        breaker.call(_boom)
    clock[0] += 6
    assert breaker.call(lambda: "probe-ok") == "probe-ok"
    assert breaker.snapshot().state is CircuitState.CLOSED
    release.set()
    pending.join(2)
    assert not pending.is_alive()
    assert results[0][0] == "error"
    assert breaker.snapshot().state is CircuitState.CLOSED
    assert breaker.snapshot().failures == 0


def test_only_one_half_open_probe_is_admitted(monkeypatch):
    import trading_gym.common.circuit_breaker as source

    clock = [10.0]
    monkeypatch.setattr(source.time, "monotonic", lambda: clock[0])
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=5)
    with pytest.raises(RuntimeError):
        breaker.call(_boom)
    clock[0] += 6
    entered, release, results = threading.Event(), threading.Event(), []

    def slow_recovery():
        entered.set()
        assert release.wait(2)
        return "healthy"

    pending = _launch(lambda: breaker.call(slow_recovery), results)
    assert entered.wait(2)
    with pytest.raises(CircuitOpenError):
        breaker.call(lambda: "second probe forbidden")
    release.set()
    pending.join(2)
    assert results == [("ok", "healthy")]
    assert breaker.snapshot().state is CircuitState.CLOSED


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1.0, 0.0])
def test_unbounded_or_nonfinite_recovery_times_are_rejected(timeout):
    with pytest.raises(ValueError, match="recovery_timeout"):
        CircuitBreaker(recovery_timeout=timeout)
