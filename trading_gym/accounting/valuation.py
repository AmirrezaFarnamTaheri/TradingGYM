"""Deterministic multi-currency account valuation.

FX quotes are explicit and time-bounded.  Conversion paths are bounded and
lexicographically deterministic.  The report is an analytical view over a
replay-verified account snapshot; it does not post accounting events or claim
tax/regulatory authority.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.accounting.models import AccountSnapshot

MAX_VALUE = Decimal("1e30")


class FXQuote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{2,20}$")]
    quote_currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{2,20}$")]
    quote_per_base: Decimal
    observed_at: datetime
    source: Annotated[str, Field(min_length=1, max_length=120)]

    @model_validator(mode="after")
    def validate_quote(self) -> "FXQuote":
        if self.base_currency == self.quote_currency:
            raise ValueError("FX quote currencies must differ")
        if (
            not self.quote_per_base.is_finite()
            or self.quote_per_base <= 0
            or self.quote_per_base > MAX_VALUE
        ):
            raise ValueError("quote_per_base must be finite, positive, and bounded")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        return self


class FXConversion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_currency: str
    target_currency: str
    rate: Decimal
    path: list[str]
    quote_sources: list[str]
    oldest_quote_at: datetime | None = None


class ValuedPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: str
    quote_currency: str
    quantity: Decimal
    mark_price: Decimal
    market_value_base: Decimal
    realized_pnl_base: Decimal
    unrealized_pnl_base: Decimal
    funding_pnl_base: Decimal
    fx_path: list[str]


class AccountValuationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    account_revision: int
    base_currency: str
    valued_at: datetime
    cash_base: Decimal
    positions_market_value_base: Decimal
    fees_base: Decimal
    realized_pnl_base: Decimal
    unrealized_pnl_base: Decimal
    funding_pnl_base: Decimal
    equity_base: Decimal
    cash_by_currency_base: dict[str, Decimal]
    positions: list[ValuedPosition]
    blockers: list[str]
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete: bool
    mutation_performed: bool = False
    regulatory_authority: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _fx_graph(
    quotes: list[FXQuote], *, valued_at: datetime, max_age_seconds: int
) -> dict[str, list[tuple[str, Decimal, str, datetime]]]:
    if len(quotes) > 100_000:
        raise ValueError("FX quote limit exceeded")
    latest: dict[tuple[str, str], FXQuote] = {}
    for quote in quotes:
        if quote.observed_at > valued_at:
            raise ValueError("FX quote is from the future")
        if (valued_at - quote.observed_at).total_seconds() > max_age_seconds:
            continue
        key = (quote.base_currency, quote.quote_currency)
        existing = latest.get(key)
        if existing is None or quote.observed_at > existing.observed_at:
            latest[key] = quote
        elif (
            quote.observed_at == existing.observed_at
            and quote.quote_per_base != existing.quote_per_base
        ):
            raise ValueError("conflicting FX quotes share the same pair and timestamp")
    graph: dict[str, list[tuple[str, Decimal, str, datetime]]] = {}
    for quote in latest.values():
        graph.setdefault(quote.base_currency, []).append(
            (
                quote.quote_currency,
                quote.quote_per_base,
                quote.source,
                quote.observed_at,
            )
        )
        graph.setdefault(quote.quote_currency, []).append(
            (
                quote.base_currency,
                Decimal("1") / quote.quote_per_base,
                quote.source,
                quote.observed_at,
            )
        )
    for edges in graph.values():
        edges.sort(key=lambda item: (item[0], item[2], item[3]))
    return graph


def resolve_fx(
    source_currency: str,
    target_currency: str,
    quotes: list[FXQuote],
    *,
    valued_at: datetime | None = None,
    max_age_seconds: int = 3600,
    max_hops: int = 4,
) -> FXConversion:
    valued_at = valued_at or datetime.now(timezone.utc)
    if valued_at.tzinfo is None or valued_at.utcoffset() is None:
        raise ValueError("valued_at must be timezone-aware")
    if (
        max_age_seconds < 1
        or max_age_seconds > 31_536_000
        or max_hops < 1
        or max_hops > 8
    ):
        raise ValueError("FX bounds are invalid")
    if source_currency == target_currency:
        return FXConversion(
            source_currency=source_currency,
            target_currency=target_currency,
            rate=Decimal("1"),
            path=[source_currency],
            quote_sources=[],
        )
    graph = _fx_graph(quotes, valued_at=valued_at, max_age_seconds=max_age_seconds)
    queue: deque[tuple[str, Decimal, list[str], list[str], list[datetime]]] = deque(
        [(source_currency, Decimal("1"), [source_currency], [], [])]
    )
    visited_depth: dict[str, int] = {source_currency: 0}
    while queue:
        current, rate, path, sources, times = queue.popleft()
        if len(path) - 1 >= max_hops:
            continue
        for destination, edge_rate, source, observed in graph.get(current, []):
            if destination in path:
                continue
            candidate_path = path + [destination]
            candidate_rate = rate * edge_rate
            candidate_sources = sources + [source]
            candidate_times = times + [observed]
            if destination == target_currency:
                return FXConversion(
                    source_currency=source_currency,
                    target_currency=target_currency,
                    rate=candidate_rate,
                    path=candidate_path,
                    quote_sources=candidate_sources,
                    oldest_quote_at=min(candidate_times),
                )
            depth = len(candidate_path) - 1
            if depth < visited_depth.get(destination, 1_000_000):
                visited_depth[destination] = depth
                queue.append(
                    (
                        destination,
                        candidate_rate,
                        candidate_path,
                        candidate_sources,
                        candidate_times,
                    )
                )
    raise KeyError(f"no fresh FX path from {source_currency} to {target_currency}")


def value_account(
    snapshot: AccountSnapshot,
    *,
    base_currency: str,
    quotes: list[FXQuote],
    valued_at: datetime | None = None,
    max_fx_age_seconds: int = 3600,
) -> AccountValuationReport:
    valued_at = valued_at or datetime.now(timezone.utc)
    blockers: list[str] = []
    cash_values: dict[str, Decimal] = {}
    conversion_cache: dict[str, FXConversion] = {}

    def conversion(currency: str) -> FXConversion | None:
        if currency in conversion_cache:
            return conversion_cache[currency]
        try:
            result = resolve_fx(
                currency,
                base_currency,
                quotes,
                valued_at=valued_at,
                max_age_seconds=max_fx_age_seconds,
            )
        except KeyError:
            blockers.append(f"missing_fx:{currency}->{base_currency}")
            return None
        conversion_cache[currency] = result
        return result

    for currency, amount in snapshot.cash.items():
        fx = conversion(currency)
        if fx is not None:
            cash_values[currency] = amount * fx.rate
    fee_total = Decimal("0")
    for currency, amount in snapshot.fees.items():
        fx = conversion(currency)
        if fx is not None:
            fee_total += amount * fx.rate

    valued_positions: list[ValuedPosition] = []
    market_total = realized_total = unrealized_total = funding_total = Decimal("0")
    for position in snapshot.positions.values():
        if position.quantity and position.mark_price is None:
            blockers.append(f"missing_mark:{position.instrument_id}")
            continue
        mark = position.mark_price or Decimal("0")
        fx = conversion(position.quote_currency)
        if fx is None:
            continue
        market = position.quantity * mark * fx.rate
        realized = position.realized_pnl * fx.rate
        unrealized = position.unrealized_pnl * fx.rate
        funding = position.funding_pnl * fx.rate
        market_total += market
        realized_total += realized
        unrealized_total += unrealized
        funding_total += funding
        valued_positions.append(
            ValuedPosition(
                instrument_id=position.instrument_id,
                quote_currency=position.quote_currency,
                quantity=position.quantity,
                mark_price=mark,
                market_value_base=market,
                realized_pnl_base=realized,
                unrealized_pnl_base=unrealized,
                funding_pnl_base=funding,
                fx_path=fx.path,
            )
        )
    cash_total = sum(cash_values.values(), Decimal("0"))
    equity = cash_total + market_total
    payload = {
        "account_id": snapshot.account_id,
        "account_revision": snapshot.revision,
        "base_currency": base_currency,
        "valued_at": valued_at.isoformat(),
        "cash_base": str(cash_total),
        "positions_market_value_base": str(market_total),
        "fees_base": str(fee_total),
        "realized_pnl_base": str(realized_total),
        "unrealized_pnl_base": str(unrealized_total),
        "funding_pnl_base": str(funding_total),
        "equity_base": str(equity),
        "cash_by_currency_base": {
            key: str(value) for key, value in sorted(cash_values.items())
        },
        "positions": [item.model_dump(mode="json") for item in valued_positions],
        "blockers": sorted(set(blockers)),
    }
    return AccountValuationReport(
        account_id=snapshot.account_id,
        account_revision=snapshot.revision,
        base_currency=base_currency,
        valued_at=valued_at,
        cash_base=cash_total,
        positions_market_value_base=market_total,
        fees_base=fee_total,
        realized_pnl_base=realized_total,
        unrealized_pnl_base=unrealized_total,
        funding_pnl_base=funding_total,
        equity_base=equity,
        cash_by_currency_base=cash_values,
        positions=valued_positions,
        blockers=sorted(set(blockers)),
        report_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
        complete=not blockers,
    )
