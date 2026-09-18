"""Paper-deterministic venue adapter (ADR-029 §6: multi-venue seam).

The second venue behind the adapter seam: a fully deterministic local
fill engine implementing the same :class:`VenueAdapter` protocol as the
live Abantether adapter. Shadow and paper autonomy levels (L0/L1) and
multi-venue rehearsal (Phase 6) run through the *same* coordinator,
mutation-retry journal, and authorization chain as live execution —
only the venue behind the seam changes.

Determinism contract: every fill is exact at the caller-supplied
reference price; identical idempotency keys return identical receipts
(``supports_idempotent_replay = True`` is therefore provable, not
claimed); order state is in-memory only and dies with the process.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Mapping

from trading_gym.production.venues import VenueAdapter, VenueOrderIntent

__all__ = ["PaperVenue"]

_SUPPORTED_OPERATIONS = frozenset({"spot.market", "order.cancel"})


class PaperVenue(VenueAdapter):
    """Deterministic local venue for shadow/paper autonomy levels."""

    name = "paper"
    supports_idempotent_replay = True

    def __init__(self, reference_prices: Mapping[str, Decimal]) -> None:
        if not reference_prices:
            raise ValueError("PaperVenue needs at least one reference price")
        normalized = {
            symbol.upper(): Decimal(str(price))
            for symbol, price in reference_prices.items()
        }
        if any(price <= 0 for price in normalized.values()):
            raise ValueError("reference prices must be positive")
        self._prices = normalized
        self._receipts: dict[str, dict[str, object]] = {}
        self._orders: dict[str, dict[str, object]] = {}
        self._halted = False

    # -- venue-agnostic panic path -------------------------------------------

    def halt(self) -> None:
        self._halted = True

    def resume(self) -> None:
        self._halted = False

    # -- VenueAdapter ---------------------------------------------------------

    def submit(self, intent: VenueOrderIntent):
        if self._halted:
            raise RuntimeError("paper venue is halted")
        if intent.venue != self.name:
            raise ValueError(
                f"intent targets venue {intent.venue!r}, adapter is {self.name!r}"
            )
        if intent.operation not in _SUPPORTED_OPERATIONS:
            raise ValueError(f"unsupported paper operation: {intent.operation}")
        if intent.idempotency_key in self._receipts:
            return self._receipts[intent.idempotency_key]

        if intent.operation == "order.cancel":
            receipt = self._cancel(intent)
        else:
            receipt = self._fill(intent)

        self._receipts[intent.idempotency_key] = receipt
        return receipt

    # -- internals --------------------------------------------------------------

    def _fill(self, intent: VenueOrderIntent) -> dict[str, object]:
        symbol = intent.symbol.upper()
        if symbol not in self._prices:
            raise ValueError(f"no paper reference price for {symbol}")
        price = self._prices[symbol]
        if intent.quantity <= 0:
            raise ValueError("quantity must be positive")
        order_id = (
            "paper-"
            + hashlib.sha256(intent.idempotency_key.encode("utf-8")).hexdigest()[:12]
        )
        receipt: dict[str, object] = {
            "order_id": order_id,
            "status": "filled",
            "price": price,
            "quantity": intent.quantity,
            "side": intent.side,
            "venue": self.name,
        }
        self._orders[order_id] = dict(receipt)
        return receipt

    def _cancel(self, intent: VenueOrderIntent) -> dict[str, object]:
        if not intent.reference_id:
            raise ValueError("order.cancel requires reference_id")
        order = self._orders.get(intent.reference_id)
        if order is None:
            raise ValueError(f"unknown paper order {intent.reference_id!r}")
        if order["status"] == "cancelled":
            receipt: dict[str, object] = {
                "order_id": intent.reference_id,
                "status": "cancelled",
                "reference_id": intent.reference_id,
                "venue": self.name,
            }
            return receipt
        order["status"] = "cancelled"
        return {
            "order_id": intent.reference_id,
            "status": "cancelled",
            "reference_id": intent.reference_id,
            "venue": self.name,
        }

    # -- introspection ------------------------------------------------------------

    @property
    def order_count(self) -> int:
        return len(self._orders)
