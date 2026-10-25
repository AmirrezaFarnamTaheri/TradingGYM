"""Stale venue read probes must never override newer circuit decisions."""

from threading import Event, Thread

import pytest

from trading_gym.market.router_app.router import RoutingPolicy, VenueRouter


class Primary:
    def __init__(self):
        self.available = False
        self.gate = None
        self.entered = None
        self.raise_after_gate = False

    def get_all_tickers(self):
        if self.entered is not None:
            self.entered.set()
            assert self.gate.wait(2)
            if self.raise_after_gate:
                raise OSError("stale probe failure")
            return ["recovered"]
        if not self.available:
            raise OSError("feed unavailable")
        return ["current"]

    def place_order(self, *args):
        raise RuntimeError("order authority failed")


class Backup:
    def get_all_tickers(self):
        return ["backup"]


def _setup(monkeypatch):
    import trading_gym.market.router_app.router as module

    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    primary = Primary()
    router = VenueRouter(
        RoutingPolicy(primary, Backup()),
        fail_open_secondary=True,
        min_error_samples=1,
        error_rate_crit=0.5,
        reset_timeout=5,
    )
    assert router.get_all_tickers() == ["backup"]
    assert router.primary_circuit_open
    clock[0] += 6
    return router, primary


def test_stale_probe_cannot_undo_new_order_circuit_failure(monkeypatch):
    router, primary = _setup(monkeypatch)
    primary.gate, primary.entered = Event(), Event()
    result = []
    thread = Thread(target=lambda: result.append(router.get_all_tickers()), daemon=True)
    thread.start()
    assert primary.entered.wait(2)
    router.reset_circuit_breaker()
    with pytest.raises(RuntimeError, match="order authority failed"):
        router.place_order("BTCUSDT", "buy", 1.0, 100.0)
    assert router.primary_circuit_open
    primary.gate.set()
    thread.join(2)
    assert not thread.is_alive()
    assert result == [["recovered"]]
    assert router.primary_circuit_open


def test_old_probe_failure_cannot_override_operator_reset(monkeypatch):
    router, primary = _setup(monkeypatch)
    primary.gate, primary.entered = Event(), Event()
    primary.raise_after_gate = True
    result = []
    thread = Thread(target=lambda: result.append(router.get_all_tickers()), daemon=True)
    thread.start()
    assert primary.entered.wait(2)
    router.reset_circuit_breaker()
    primary.gate.set()
    thread.join(2)
    assert not thread.is_alive()
    assert result == [["backup"]]
    assert not router.primary_circuit_open
