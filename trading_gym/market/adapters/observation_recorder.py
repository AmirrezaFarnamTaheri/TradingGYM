"""Read-only advertised quote/fee/limit observations for research admission.

No client is constructed here. Callers inject the existing read adapter; this
module calls only get_ticker and get_coin_info. It never reads balances, orders,
fills or credentials and never submits/cancels an order. Admission is research
data quality, not live authority or proof of executable venue liquidity.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import statistics
import time
from typing import Protocol
from uuid import uuid4

from filelock import FileLock
from trading_gym.market.adapters.enhanced_aban_client import OrderSide
from trading_gym.core.persistence import atomic_write_json
from trading_gym.core.security import confined_child


class ReadMarketClient(Protocol):
    def get_ticker(self, symbol: str): ...
    def get_coin_info(self, symbol: str, side: OrderSide): ...


@dataclass(frozen=True)
class ObservationProfile:
    source: str
    source_version: str
    symbol: str
    quote_currency: str
    mapping_reference: str | None = None
    fee_unit: str = "unknown"
    max_quote_age_seconds: float = 30.0
    max_read_duration_seconds: float = 10.0

    def __post_init__(self):
        for value in (
            self.source,
            self.source_version,
            self.symbol,
            self.quote_currency,
        ):
            if not re.fullmatch(r"[A-Za-z0-9._/-]{1,80}", value):
                raise ValueError("invalid source/profile identifier")
        if self.quote_currency not in {"USDT", "IRT"}:
            raise ValueError("unsupported quote currency profile")
        if self.fee_unit not in {"fraction", "percent", "basis_points", "unknown"}:
            raise ValueError("unsupported fee unit")
        if (
            not 0 < self.max_quote_age_seconds <= 3600
            or not 0 < self.max_read_duration_seconds <= 120
        ):
            raise ValueError("invalid freshness budget")
        if self.mapping_reference is not None and not re.fullmatch(
            r"[A-Za-z0-9._:/-]{1,200}", self.mapping_reference
        ):
            raise ValueError("invalid mapping reference")


def _decimal(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (ValueError, InvalidOperation):
        return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def capture(
    client: ReadMarketClient,
    profile: ObservationProfile,
    *,
    observed_quote_currency: str | None = None,
    source_event_time: datetime | None = None,
    event_time_source: str | None = None,
    now=lambda: datetime.now(timezone.utc),
    monotonic=time.monotonic,
) -> dict:
    if source_event_time is not None and (
        not event_time_source
        or not re.fullmatch(r"[A-Za-z0-9._/-]{1,80}", event_time_source)
    ):
        raise ValueError("a source event timestamp requires its provenance field")
    started = monotonic()
    reasons: list[str] = []
    selected = {"ticker": {}, "fees_limits": {}}
    received = {}
    try:
        ticker = client.get_ticker(profile.symbol)
        received["ticker"] = _utc(now()).isoformat()
        selected["ticker"] = {
            name: getattr(ticker, name, None)
            for name in (
                "symbol",
                "buy_price",
                "sell_price",
                "buy_max",
                "sell_max",
                "active",
            )
        }
        for side in (OrderSide.BUY, OrderSide.SELL):
            info = client.get_coin_info(profile.symbol, side)
            received[f"limits_{side.value}"] = _utc(now()).isoformat()
            selected["fees_limits"][side.value] = {
                name: getattr(info, name, None)
                for name in (
                    "symbol",
                    "exchange_fee",
                    "usdt_min_trade",
                    "usdt_max_trade",
                    "irt_min_trade",
                    "irt_max_trade",
                    "irt_fee",
                    "tether_fee",
                )
            }
    except Exception as exc:
        reasons.append("read_failed:" + type(exc).__name__)
    elapsed = monotonic() - started
    captured = _utc(now())
    event = _utc(source_event_time) if source_event_time else None
    age = (captured - event).total_seconds() if event else None
    if age is None:
        reasons.append("source_event_time_unknown")
    elif age < 0:
        reasons.append("source_event_time_in_future")
    elif age > profile.max_quote_age_seconds:
        reasons.append("stale_quote")
    if elapsed < 0 or elapsed > profile.max_read_duration_seconds:
        reasons.append("read_duration_exceeded")
    if observed_quote_currency is None:
        reasons.append("quote_currency_unverified")
    elif observed_quote_currency != profile.quote_currency:
        reasons.append("quote_currency_mismatch")
    if profile.mapping_reference is None:
        reasons.append("price_fee_limit_mapping_unverified")
    if profile.fee_unit == "unknown":
        reasons.append("fee_unit_unknown")
    raw_ticker = selected["ticker"]
    if raw_ticker.get("symbol") != profile.symbol:
        reasons.append("instrument_mismatch")
    if raw_ticker.get("active") is not True:
        reasons.append("market_inactive_or_unknown")
    # Existing adapter spread property uses customer buy minus customer sell.
    # Preserve that declared mapping, with provenance required for admission.
    ask, bid = _decimal(raw_ticker.get("buy_price")), _decimal(
        raw_ticker.get("sell_price")
    )
    spread_bps = None
    if ask is None or bid is None or ask <= 0 or bid <= 0 or bid > ask:
        reasons.append("invalid_or_inverted_quote")
    else:
        spread_bps = str((ask - bid) / ((ask + bid) / 2) * 10_000)
    for side in ("buy", "sell"):
        info = selected["fees_limits"].get(side, {})
        prefix = profile.quote_currency.lower()
        minimum, maximum = _decimal(info.get(f"{prefix}_min_trade")), _decimal(
            info.get(f"{prefix}_max_trade")
        )
        if minimum is None or maximum is None or minimum <= 0 or maximum < minimum:
            reasons.append(f"missing_or_invalid_limits:{side}")
        if info.get("symbol") != profile.symbol:
            reasons.append(f"limits_instrument_mismatch:{side}")
        fee = _decimal(info.get("exchange_fee"))
        scale = {
            "fraction": Decimal(10_000),
            "percent": Decimal(100),
            "basis_points": Decimal(1),
        }.get(profile.fee_unit)
        if fee is None or fee < 0 or (scale is not None and fee * scale >= 10_000):
            reasons.append(f"missing_or_invalid_advertised_fee:{side}")
    # Hash only whitelisted, normalized adapter fields, not an imagined raw HTTP body.
    normalized = json.loads(
        json.dumps(selected, default=str, sort_keys=True, allow_nan=False)
    )
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "observation_id": uuid4().hex,
        "source": profile.source,
        "source_version": profile.source_version,
        "symbol": profile.symbol,
        "quote_currency": profile.quote_currency,
        "observed_quote_currency": observed_quote_currency,
        "mapping_reference": profile.mapping_reference,
        "fee_unit": profile.fee_unit,
        "captured_at_utc": captured.isoformat(),
        "received_at_utc": received,
        "source_event_at_utc": event.isoformat() if event else None,
        "event_time_source": event_time_source if event else "not_supplied_by_adapter",
        "event_age_seconds": age,
        "read_duration_seconds": elapsed,
        "normalized_fields_sha256": digest,
        "normalized_fields": normalized,
        "spread_bps": spread_bps,
        "admission": "research_observation" if not reasons else "modeled_cost_required",
        "reasons": reasons,
        "own_order_fills_observed": False,
        "live_authority": False,
    }


def persist(root: Path, observation: dict, *, max_records: int = 10_000) -> Path:
    if not 1 <= max_records <= 100_000:
        raise ValueError("invalid observation record bound")
    identifier = observation.get("observation_id", "")
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ValueError("invalid observation identifier")
    encoded = json.dumps(observation, allow_nan=False).encode()
    if len(encoded) > 32 * 1024:
        raise ValueError("observation exceeds 32 KiB")
    if root.is_symlink():
        raise ValueError("observation root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with FileLock(str(root / ".observations.lock")):
        count = 0
        for path in root.iterdir():
            if path.suffix == ".json":
                count += 1
            if count >= max_records:
                raise ValueError("observation store record limit reached")
        destination = confined_child(root, f"{identifier}.json", allow_symlinks=False)
        if destination.exists():
            raise ValueError("observation is immutable")
        atomic_write_json(destination, observation)
    return destination


def coverage(observations: list[dict]) -> dict:
    if len(observations) > 1000:
        raise ValueError("coverage batch exceeds 1000 observations")
    reasons = Counter(reason for row in observations for reason in row["reasons"])
    admitted = [
        row for row in observations if row["admission"] == "research_observation"
    ]
    spreads = [
        float(row["spread_bps"]) for row in admitted if row["spread_bps"] is not None
    ]
    return {
        "observations": len(observations),
        "admitted": len(admitted),
        "source_time_known": sum(
            row["source_event_at_utc"] is not None for row in observations
        ),
        "downgrade_reasons": dict(reasons),
        "admitted_spread_bps_median": statistics.median(spreads) if spreads else None,
        "own_order_fill_or_latency_evidence": False,
    }
