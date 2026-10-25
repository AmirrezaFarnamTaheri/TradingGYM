"""P&L component decomposition with an honest Residual bucket.

Absorbed mechanism: kraken-replay's `explain` splits realized P&L into named
components — price movement, fees, funding, spread/slippage — plus a
**Residual** bucket that is the honest catch-all for unexplained money
(`crates/kraken-replay/src/explain.rs:567-604`).  A non-zero Residual is not a
rounding note; it is a flag that the tape and the books disagree.

This port works on the authoritative accounting plane: it takes the opening
cash, the current :class:`~trading_gym.execution.accounting.models.AccountSnapshot`, and
the set of *known* external cash adjustments (deposits/withdrawals recorded as
CASH_ADJUSTMENT events).  Anything the components cannot explain lands in
``residual``; ``balanced`` is False whenever that exceeds tolerance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trading_gym.execution.accounting.models import AccountSnapshot

ZERO = Decimal("0")

COMPONENT_REALIZED = "realized_price_movement"
COMPONENT_FEES = "fees"
COMPONENT_FUNDING = "funding"
COMPONENT_EXTERNAL = "external_adjustments"
COMPONENT_UNREALIZED = "unrealized_mark_to_market"
COMPONENT_RESIDUAL = "residual_unexplained"


@dataclass(frozen=True)
class CurrencyDecomposition:
    currency: str
    opening_cash: Decimal
    closing_cash: Decimal
    realized_price_movement: Decimal
    fees: Decimal
    funding: Decimal
    external_adjustments: Decimal
    unrealized_mark_to_market: Decimal
    residual_unexplained: Decimal
    balanced: bool


@dataclass(frozen=True)
class DecompositionReport:
    currencies: dict[str, CurrencyDecomposition]
    balanced: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


def decompose_snapshot(
    snapshot: AccountSnapshot,
    *,
    opening_cash: dict[str, Decimal],
    known_external_adjustments: dict[str, Decimal] | None = None,
    tolerance: Decimal = Decimal("0.01"),
) -> DecompositionReport:
    """Explain ``snapshot.cash - opening_cash`` per currency.

    Identity enforced per currency::

        cash_change = realized + funding - fees + external + residual

    Fees are stored positive in the snapshot but reduce cash, hence the minus.
    Unrealized P&L is reported separately (mark-to-market, not yet cash).
    ``known_external_adjustments`` are caller-declared deposits/withdrawals;
    anything left over falls into ``residual_unexplained`` and flips
    ``balanced`` to False past ``tolerance``.
    """
    if tolerance < ZERO:
        raise ValueError("tolerance must be non-negative")
    adjustments = {k.upper(): v for k, v in (known_external_adjustments or {}).items()}

    currencies = sorted(set(snapshot.cash) | set(opening_cash) | set(adjustments))
    out: dict[str, CurrencyDecomposition] = {}
    all_balanced = True
    notes: list[str] = []

    for currency in currencies:
        opening = opening_cash.get(currency, ZERO)
        closing = snapshot.cash.get(currency, ZERO)
        cash_change = closing - opening

        realized = sum(
            (
                p.realized_pnl
                for p in snapshot.positions.values()
                if p.quote_currency == currency
            ),
            ZERO,
        )
        funding = sum(
            (
                p.funding_pnl
                for p in snapshot.positions.values()
                if p.quote_currency == currency
            ),
            ZERO,
        )
        unrealized = sum(
            (
                p.unrealized_pnl if p.unrealized_pnl is not None else ZERO
                for p in snapshot.positions.values()
                if p.quote_currency == currency
            ),
            ZERO,
        )
        fees = snapshot.fees.get(currency, ZERO)
        external = adjustments.get(currency, ZERO)

        residual = cash_change - (realized + funding - fees + external)
        balanced = abs(residual) <= tolerance
        all_balanced = all_balanced and balanced
        if not balanced:
            notes.append(
                f"{currency}: residual {residual} exceeds tolerance {tolerance}; "
                "the tape and the books disagree"
            )

        out[currency] = CurrencyDecomposition(
            currency=currency,
            opening_cash=opening,
            closing_cash=closing,
            realized_price_movement=realized,
            fees=fees,
            funding=funding,
            external_adjustments=external,
            unrealized_mark_to_market=unrealized,
            residual_unexplained=residual,
            balanced=balanced,
        )

    return DecompositionReport(
        currencies=out, balanced=all_balanced, notes=tuple(notes)
    )
