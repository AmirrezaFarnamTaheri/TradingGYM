from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float


class VenueAdapter(ABC):
    """
    Abstract base class for a venue adapter.
    Defines the interface for interacting with an exchange.
    """

    @abstractmethod
    def get_ticker(self, symbol: str) -> Optional[Ticker]:
        """Fetches the ticker for a single symbol."""
        raise NotImplementedError("Subclasses must implement get_ticker")

    @abstractmethod
    def get_all_tickers(self) -> List[Ticker]:
        """Fetches tickers for all supported symbols."""
        raise NotImplementedError("Subclasses must implement get_all_tickers")

    @abstractmethod
    def place_order(self, symbol: str, side: str, qty: float, price: float) -> str:
        """Places an order and returns the order ID."""
        raise NotImplementedError("Subclasses must implement place_order")

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancels an order and returns True if successful."""
        raise NotImplementedError("Subclasses must implement cancel_order")
