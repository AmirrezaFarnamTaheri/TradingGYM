"""Venue routing: resilient market reads, explicitly non-failover order writes.

Reading a ticker is repeatable; placing an order is not. A failed read may use
an optional secondary venue immediately, but an uncertain write MUST stay with
its originally selected authority for reconciliation. Never turn an open read
circuit into permission to place a trade on a different account.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from typing import Callable, List, Optional, TypeVar

from trading_gym.market.router_app.adapters.base import Ticker, VenueAdapter

logger = logging.getLogger(__name__)
T = TypeVar("T")


class RoutingPolicy:
    """Select the primary for execution; secondary is a read-only fallback."""

    def __init__(
        self,
        primary_venue: VenueAdapter,
        secondary_venue: Optional[VenueAdapter] = None,
    ):
        self.primary = primary_venue
        self.secondary = secondary_venue

    def get_execution_venue(self, symbol: str) -> VenueAdapter:
        return self.primary


class VenueRouter:
    """Bounded read-only failover and cautious primary circuit recovery.

    ``fail_open_secondary`` is retained for API compatibility and controls
    *reads only*. This router never automatically fails over venue mutations.
    A single successful primary read probe after cooldown closes the circuit;
    other concurrent requests use the secondary until that probe concludes.
    """

    def __init__(
        self,
        policy: RoutingPolicy,
        error_rate_crit: float = 0.5,
        fail_open_secondary: bool = False,
        reset_timeout: float = 60,
        *,
        min_error_samples: int = 10,
        sample_window: int = 20,
    ):
        if not math.isfinite(error_rate_crit) or not 0 < error_rate_crit <= 1:
            raise ValueError("error_rate_crit must be finite and in (0, 1]")
        if not math.isfinite(reset_timeout) or reset_timeout <= 0:
            raise ValueError("reset_timeout must be finite and positive")
        if not 1 <= min_error_samples <= sample_window <= 10_000:
            raise ValueError("invalid circuit sample window")
        self.policy = policy
        self.error_rate_crit = error_rate_crit
        self.fail_open_secondary = fail_open_secondary
        self.reset_timeout = reset_timeout
        self.primary_error_count = 0
        self.primary_total_count = 0
        self.primary_circuit_open = False
        self.last_error_time = 0.0
        self.last_reset_time = 0.0
        self._samples: deque[bool] = deque(maxlen=sample_window)
        self._min_error_samples = min_error_samples
        self._opened_at = 0.0
        self._probe_in_flight = False
        self._generation = 0
        self._lock = threading.Lock()

    def _read(self, request: Callable[[VenueAdapter], T]) -> T:
        primary = self.policy.primary
        with self._lock:
            opened = self.primary_circuit_open
            probing = opened and (
                not self._probe_in_flight
                and time.monotonic() - self._opened_at >= self.reset_timeout
            )
            if probing:
                self._probe_in_flight = True
            use_primary = not opened or probing
            generation = self._generation
        if use_primary:
            try:
                value = request(primary)
            except Exception as exc:
                self._record_failure(primary, probe=probing, generation=generation)
                if not (self.fail_open_secondary and self.policy.secondary):
                    raise
                logger.warning(
                    "Primary market-data read failed error_type=%s", type(exc).__name__
                )
            else:
                self._record_success(primary, probe=probing, generation=generation)
                return value
        if self.fail_open_secondary and self.policy.secondary:
            # Read-only fallback is safe; it is not permission to switch the
            # execution venue (which may be holding an unacknowledged order).
            return request(self.policy.secondary)
        raise RuntimeError("primary venue read circuit is open")

    def get_all_tickers(self) -> List[Ticker]:
        return self._read(lambda venue: venue.get_all_tickers())

    def get_ticker(self, symbol: str) -> Optional[Ticker]:
        return self._read(lambda venue: venue.get_ticker(symbol))

    def place_order(self, symbol: str, side: str, qty: float, price: float) -> str:
        """Never reroute an order merely because the primary is unavailable."""
        venue = self.policy.get_execution_venue(symbol)
        with self._lock:
            if venue is self.policy.primary and self.primary_circuit_open:
                raise RuntimeError("primary venue order circuit is open")
            generation = self._generation
        try:
            order_id = venue.place_order(symbol, side, qty, price)
        except Exception:
            self._record_failure(venue, generation=generation)
            raise
        self._record_success(venue, generation=generation)
        return order_id

    def _record_success(
        self, venue: VenueAdapter, *, probe: bool = False, generation: int | None = None
    ) -> None:
        if venue is not self.policy.primary:
            return
        with self._lock:
            if generation is not None and generation != self._generation:
                return
            if probe:
                self._reset_unlocked()
            else:
                self._samples.append(False)
                self.primary_total_count += 1

    def _record_failure(
        self, venue: VenueAdapter, *, probe: bool = False, generation: int | None = None
    ) -> None:
        if venue is not self.policy.primary:
            return
        with self._lock:
            if generation is not None and generation != self._generation:
                return
            self.primary_total_count += 1
            self.primary_error_count += 1
            self.last_error_time = time.monotonic()
            self._samples.append(True)
            if probe or (
                len(self._samples) >= self._min_error_samples
                and sum(self._samples) / len(self._samples) > self.error_rate_crit
            ):
                self.primary_circuit_open = True
                self._opened_at = time.monotonic()
                self._probe_in_flight = False
                self._generation += 1
                logger.error("Primary venue circuit opened")

    def _reset_unlocked(self) -> None:
        self.primary_circuit_open = False
        self.primary_error_count = 0
        self.primary_total_count = 0
        self._samples.clear()
        self._probe_in_flight = False
        self._generation += 1
        self.last_reset_time = time.monotonic()

    def reset_circuit_breaker(self) -> None:
        """Explicit operator reset, not an automatic order-failover signal."""
        with self._lock:
            self._reset_unlocked()
