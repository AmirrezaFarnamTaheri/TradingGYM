"""Balance reservations for resting orders.

Absorbed mechanism: limit orders reserve ``cost × (1 + fee)`` up front and
release on cancel/expiry so the same funds cannot be double-committed across
resting orders (kraken-paper `lib.rs:585-592,867` design, target-native
Decimal port).

The ledger is a pure state machine over an append-only event list; every
transition is derivable from the events, which makes it replay-verified by
construction and safe to persist alongside the accounting plane.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from threading import RLock
from typing import Any

ZERO = Decimal("0")


class ReservationError(RuntimeError):
    """Reservation request conflicts with ledger state."""


@dataclass(frozen=True)
class LedgerEvent:
    kind: str  # reserve | release | commit | genesis
    order_id: str
    currency: str
    amount: Decimal
    sequence: int


class ReservationLedger:
    """Tracks per-currency reserved amounts against opening balances."""

    def __init__(self, opening_balances: dict[str, Decimal]):
        self._lock = RLock()
        self._opening: dict[str, Decimal] = {}
        for currency, amount in opening_balances.items():
            currency_u = currency.upper()
            if not currency_u or len(currency_u) > 20:
                raise ValueError("currency codes must be 1-20 characters")
            if (
                not isinstance(amount, Decimal)
                or not amount.is_finite()
                or amount < ZERO
            ):
                raise ValueError(
                    "opening balances must be finite non-negative Decimals"
                )
            if currency_u in self._opening:
                raise ValueError("duplicate_normalized_currency")
            self._opening[currency_u] = amount
        self._reserved_by_order: dict[str, dict[str, Decimal]] = {}
        # A released or settled order ID is never a new reservation identity.
        self._used_order_ids: set[str] = set()
        self._committed: dict[str, Decimal] = {c: ZERO for c in self._opening}
        self._events: list[LedgerEvent] = []
        self._sequence = 0
        for currency in sorted(self._opening):
            if self._opening[currency] > ZERO:
                self._append("genesis", "-", currency, self._opening[currency])

    # -- internals ---------------------------------------------------------

    def _append(self, kind: str, order_id: str, currency: str, amount: Decimal) -> None:
        self._sequence += 1
        self._events.append(
            LedgerEvent(
                kind=kind,
                order_id=order_id,
                currency=currency,
                amount=amount,
                sequence=self._sequence,
            )
        )

    def _active_reserved(self, currency: str) -> Decimal:
        total = ZERO
        for per_order in self._reserved_by_order.values():
            total += per_order.get(currency, ZERO)
        return total

    def _require_currency(self, currency: str) -> str:
        currency_u = currency.upper()
        if currency_u not in self._opening:
            raise ReservationError(f"unknown currency {currency_u}")
        return currency_u

    # -- public API --------------------------------------------------------

    def reserve(self, *, order_id: str, currency: str, amount: Decimal) -> None:
        """Reserve ``amount`` for ``order_id``; refuses over-commitment."""
        with self._lock:
            if not order_id:
                raise ValueError("order_id is required")
            if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= ZERO:
                raise ValueError("reservation amount must be a finite positive Decimal")
            currency_u = self._require_currency(currency)
            if order_id in self._used_order_ids:
                raise ReservationError(f"order {order_id} already used for reservation")
            available = self.available_balance(currency_u)
            if amount > available:
                raise ReservationError(
                    f"insufficient available {currency_u}: requested {amount}, "
                    f"available {available}"
                )
            self._reserved_by_order[order_id] = {currency_u: amount}
            self._used_order_ids.add(order_id)
            self._append("reserve", order_id, currency_u, amount)

    def release(self, *, order_id: str) -> bool:
        """Release an active reservation. Idempotent; True when released now."""
        with self._lock:
            per_order = self._reserved_by_order.pop(order_id, None)
            if per_order is None:
                return False
            for currency, amount in sorted(per_order.items()):
                if amount > ZERO:
                    self._append("release", order_id, currency, amount)
            return True

    def commit_settlement(
        self, *, order_id: str, currency: str, settled_amount: Decimal
    ) -> Decimal:
        """Settle a filled order: releases its reservation and debits.

        Returns the additional amount debited beyond the reservation (the
        fee/slippage top-up). Settled amounts above reservation + available
        are refused — settlement may never mint money.
        """
        with self._lock:
            currency_u = self._require_currency(currency)
            per_order = self._reserved_by_order.get(order_id)
            if per_order is None or per_order.get(currency_u, ZERO) <= ZERO:
                raise ReservationError(
                    f"order {order_id} holds no {currency_u} reservation"
                )
            if (
                not isinstance(settled_amount, Decimal)
                or not settled_amount.is_finite()
                or settled_amount <= ZERO
            ):
                raise ValueError("settled_amount must be a finite positive Decimal")
            reserved = per_order[currency_u]
            extra_needed = max(ZERO, settled_amount - reserved)
            if extra_needed > self.available_balance(currency_u):
                raise ReservationError(
                    f"settlement {settled_amount} exceeds reservation {reserved} "
                    f"plus available {self.available_balance(currency_u)}"
                )
            self.release(order_id=order_id)
            self._committed[currency_u] = (
                self._committed.get(currency_u, ZERO) + settled_amount
            )
            self._append("commit", order_id, currency_u, settled_amount)
            return extra_needed

    # -- views -------------------------------------------------------------

    def available_balance(self, currency: str) -> Decimal:
        with self._lock:
            currency_u = self._require_currency(currency)
            return (
                self._opening[currency_u]
                - self._committed.get(currency_u, ZERO)
                - self._active_reserved(currency_u)
            )

    def reserved_for_order(self, order_id: str) -> dict[str, Decimal]:
        with self._lock:
            return dict(self._reserved_by_order.get(order_id, {}))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            currencies = sorted(set(self._opening) | set(self._committed))
            return {
                currency: {
                    "opening": self._opening[currency],
                    "committed": self._committed.get(currency, ZERO),
                    "reserved": self._active_reserved(currency),
                    "available": (
                        self._opening[currency]
                        - self._committed.get(currency, ZERO)
                        - self._active_reserved(currency)
                    ),
                }
                for currency in currencies
            }

    def events_digest(self) -> str:
        with self._lock:
            payload = json.dumps(
                [
                    [e.sequence, e.kind, e.order_id, e.currency, str(e.amount)]
                    for e in self._events
                ],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            return hashlib.sha256(payload).hexdigest()
