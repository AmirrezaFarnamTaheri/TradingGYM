from typing import List, Optional

from trading_gym.market.router_app.adapters.base import Ticker, VenueAdapter
from trading_gym.market.router_app.router import RoutingPolicy, VenueRouter


class FailingVenue(VenueAdapter):
    name = "failing"

    def get_ticker(self, symbol: str) -> Optional[Ticker]:
        raise RuntimeError("network error")

    def get_all_tickers(self) -> List[Ticker]:
        raise RuntimeError("network error")

    def place_order(self, symbol: str, side: str, qty: float, price: float) -> str:
        raise RuntimeError("unreachable")

    def cancel_order(self, order_id: str) -> bool:
        raise RuntimeError("unreachable")


class DummyVenue(VenueAdapter):
    name = "ok"

    def get_ticker(self, symbol: str) -> Optional[Ticker]:
        return None

    def get_all_tickers(self) -> List[Ticker]:
        return []

    def place_order(self, symbol: str, side: str, qty: float, price: float) -> str:
        return '{"status": "ok"}'

    def cancel_order(self, order_id: str) -> bool:
        return True


def test_router_opens_circuit_after_error_threshold() -> None:
    primary = FailingVenue()
    secondary = DummyVenue()
    policy = RoutingPolicy(primary_venue=primary, secondary_venue=secondary)
    router = VenueRouter(policy=policy, error_rate_crit=0.05, fail_open_secondary=False)

    # 25 failures should exceed the 5% error rate after the warmup window (20 calls)
    for _ in range(25):
        try:
            _ = router.get_all_tickers()
        except RuntimeError:
            pass

    assert router.primary_circuit_open is True


def test_read_only_failover_on_first_primary_failure() -> None:
    primary = FailingVenue()
    secondary = DummyVenue()
    router = VenueRouter(RoutingPolicy(primary, secondary), fail_open_secondary=True)
    assert router.get_all_tickers() == []
    assert router.get_ticker("BTCUSDT") is None
    assert router.primary_error_count == 2


def test_secondary_is_not_a_mutation_fallback_after_circuit_opens() -> None:
    class Recorder(DummyVenue):
        def __init__(self):
            self.orders = []

        def place_order(self, symbol, side, qty, price):
            self.orders.append((symbol, side, qty, price))
            return "unexpected"

    primary = FailingVenue()
    secondary = Recorder()
    router = VenueRouter(
        RoutingPolicy(primary, secondary),
        fail_open_secondary=True,
        error_rate_crit=0.05,
        min_error_samples=2,
    )
    assert router.get_all_tickers() == []
    assert router.get_all_tickers() == []
    assert router.primary_circuit_open
    import pytest

    with pytest.raises(RuntimeError, match="order circuit is open"):
        router.place_order("BTCUSDT", "buy", 1, 100)
    assert secondary.orders == []


def test_recovery_probes_primary_and_restores_best_data(monkeypatch) -> None:
    class Recovering(DummyVenue):
        def __init__(self):
            self.available = False
            self.calls = 0

        def get_all_tickers(self):
            self.calls += 1
            if not self.available:
                raise OSError("down")
            return [Ticker("BTCUSDT", 99, 101, 100, 10)]

    clock = [100.0]
    monkeypatch.setattr("trading_gym.market.router_app.router.time.monotonic", lambda: clock[0])
    primary = Recovering()
    backup = DummyVenue()
    router = VenueRouter(
        RoutingPolicy(primary, backup),
        fail_open_secondary=True,
        reset_timeout=10,
        min_error_samples=1,
        error_rate_crit=0.5,
    )
    assert router.get_all_tickers() == []
    assert router.primary_circuit_open
    assert router.get_all_tickers() == []
    assert primary.calls == 1
    clock[0] += 11
    primary.available = True
    quote = router.get_all_tickers()
    assert len(quote) == 1
    assert quote[0].symbol == "BTCUSDT"
    assert not router.primary_circuit_open
    assert primary.calls == 2
    assert router.get_all_tickers() == quote


def test_failed_recovery_probe_reopens_and_stays_on_backup(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("trading_gym.market.router_app.router.time.monotonic", lambda: clock[0])
    primary, backup = FailingVenue(), DummyVenue()
    router = VenueRouter(
        RoutingPolicy(primary, backup),
        fail_open_secondary=True,
        reset_timeout=5,
        min_error_samples=1,
    )
    router.get_all_tickers()
    assert router.primary_circuit_open
    clock[0] += 6
    assert router.get_all_tickers() == []
    assert router.primary_circuit_open
    assert router.get_all_tickers() == []


def test_router_rejects_unbounded_invalid_policy() -> None:
    import pytest

    for kwargs in (
        {"error_rate_crit": float("nan")},
        {"error_rate_crit": 0.0},
        {"reset_timeout": -1},
        {"sample_window": 0},
        {"min_error_samples": 21, "sample_window": 20},
    ):
        with pytest.raises(ValueError):
            VenueRouter(RoutingPolicy(DummyVenue()), **kwargs)
