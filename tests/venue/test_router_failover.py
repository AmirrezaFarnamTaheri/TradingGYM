from typing import List, Optional

from apps.venue.adapters.base import Ticker, VenueAdapter
from apps.venue.router import RoutingPolicy, VenueRouter


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
