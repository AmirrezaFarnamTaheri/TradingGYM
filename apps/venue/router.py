from typing import List, Optional
from apps.venue.adapters.base import VenueAdapter, Ticker
import time
import logging

logger = logging.getLogger(__name__)


class RoutingPolicy:
    """
    A simple routing policy that always prefers the primary venue.
    """

    def __init__(
        self,
        primary_venue: VenueAdapter,
        secondary_venue: Optional[VenueAdapter] = None,
    ):
        self.primary = primary_venue
        self.secondary = secondary_venue

    def get_execution_venue(self, symbol: str) -> VenueAdapter:
        """Returns the preferred venue for execution."""
        return self.primary


class VenueRouter:
    """
    Routes orders to the appropriate venue based on a routing policy.
    Includes a circuit breaker to handle venue failures.
    """

    def __init__(
        self,
        policy: RoutingPolicy,
        error_rate_crit: float = 0.5,
        fail_open_secondary: bool = False,
        reset_timeout: int = 60,
    ):
        self.policy = policy
        self.error_rate_crit = error_rate_crit
        self.fail_open_secondary = fail_open_secondary
        self.reset_timeout = reset_timeout

        self.primary_error_count: int = 0
        self.primary_total_count: int = 0
        self.primary_circuit_open: bool = False
        self.last_error_time: float = 0.0
        self.last_reset_time: float = 0.0

    def get_all_tickers(self) -> List[Ticker]:
        """
        Gets all tickers, routing the request to the best venue.
        """
        self._check_and_reset_circuit_breaker()
        venue = self.policy.primary

        if self.primary_circuit_open and venue == self.policy.primary:
            if self.policy.secondary and self.fail_open_secondary:
                logger.warning(
                    "Primary venue circuit is open, failing over to secondary."
                )
                venue = self.policy.secondary
            else:
                raise RuntimeError("Primary venue circuit is open.")

        try:
            tickers = venue.get_all_tickers()
            self._record_success(venue)
            return tickers
        except Exception as e:
            self._record_failure(venue)
            raise e

    def place_order(self, symbol: str, side: str, qty: float, price: float) -> str:
        """
        Places an order, routing it to the best venue.
        """
        self._check_and_reset_circuit_breaker()
        venue = self.policy.get_execution_venue(symbol)

        if self.primary_circuit_open and venue == self.policy.primary:
            if self.policy.secondary and self.fail_open_secondary:
                logger.warning(
                    "Primary venue circuit is open, failing over to secondary."
                )
                venue = self.policy.secondary
            else:
                raise RuntimeError("Primary venue circuit is open.")

        try:
            order_id = venue.place_order(symbol, side, qty, price)
            self._record_success(venue)
            return order_id
        except Exception as e:
            self._record_failure(venue)
            raise e

    def _record_success(self, venue: VenueAdapter):
        if venue == self.policy.primary:
            self.primary_total_count += 1

    def _record_failure(self, venue: VenueAdapter):
        if venue == self.policy.primary:
            self.primary_total_count += 1
            self.primary_error_count += 1
            self.last_error_time = time.time()
            self._check_circuit_breaker()

    def _check_circuit_breaker(self):
        if self.primary_total_count < 10:
            return

        error_rate = self.primary_error_count / self.primary_total_count
        if error_rate > self.error_rate_crit:
            self.primary_circuit_open = True
            logger.error("CIRCUIT BREAKER: Primary venue circuit is now open.")

    def _check_and_reset_circuit_breaker(self):
        if self.primary_circuit_open and (
            time.time() - self.last_error_time > self.reset_timeout
        ):
            self.reset_circuit_breaker()

    def reset_circuit_breaker(self):
        self.primary_circuit_open = False
        self.primary_error_count = 0
        self.primary_total_count = 0
        self.last_reset_time = time.time()
        logger.info("CIRCUIT BREAKER: Primary venue circuit has been reset.")
