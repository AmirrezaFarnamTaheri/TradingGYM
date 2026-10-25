from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class VenueOrderIntent:
    """One immutable, idempotent venue mutation request."""

    venue: str
    operation: str
    base_symbol: str
    quote_symbol: str
    side: str
    quantity: Decimal
    idempotency_key: str
    reference_id: str = ""

    @property
    def symbol(self) -> str:
        return f"{self.base_symbol}{self.quote_symbol}"


class VenueAdapter:
    name = "abstract"

    #: Whether the upstream venue deduplicates repeated submissions carrying
    #: the same idempotency key.  Only venues that provably replay-dedup may
    #: set this to True; it gates automatic retry after ambiguous failures.
    supports_idempotent_replay: bool = False

    def submit(self, intent: VenueOrderIntent):
        raise NotImplementedError


class AbanTetherVenue(VenueAdapter):
    name = "abantether"

    def __init__(self, transport):
        self.transport = transport

    def submit(self, intent: VenueOrderIntent):
        return self.transport.submit_order(intent)
