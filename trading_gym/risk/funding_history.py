"""Offline, point-in-time settled-funding evidence, never a cash ledger.

Unlike historical replay helpers that substitute the earliest *future* rate,
this evaluator requires both the effective time and actual observation time
to precede its as-of timestamp. It neither requests venue data nor mutates
positions, broker state, or research inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
import re
from typing import Literal, Sequence

_INSTRUMENT = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,119}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_MAX_EVENTS = 10_000


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("funding timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class FundingObservation:
    instrument_id: str
    effective_at: datetime
    observed_at: datetime
    rate: Decimal
    source_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not _INSTRUMENT.fullmatch(self.instrument_id):
            raise ValueError("invalid funding instrument")
        effective, observed = _utc(self.effective_at), _utc(self.observed_at)
        if observed < effective:
            raise ValueError("observed_at cannot precede settled rate effective_at")
        if type(self.rate) is not Decimal or not self.rate.is_finite() or abs(self.rate) > 1:
            raise ValueError("rate must be a finite bounded Decimal")
        if not isinstance(self.source_sha256, str) or not _SHA256.fullmatch(self.source_sha256):
            raise ValueError("source_sha256 must be lowercase hexadecimal SHA-256")


@dataclass(frozen=True)
class FundingResolution:
    instrument_id: str
    as_of: datetime
    effective_at: datetime | None
    rate: Decimal | None
    status: Literal["available", "unavailable", "stale"]
    eligible_count: int
    evidence_sha256: str
    capital_authority_granted: bool = False


def resolve_funding_history(
    observations: Sequence[FundingObservation],
    *,
    instrument_id: str,
    as_of: datetime,
    max_age: timedelta = timedelta(hours=12),
) -> FundingResolution:
    """Report the latest observed settled rate available at as_of.

    Only the eligible prefix contributes to the receipt, so injecting an
    observation from the future cannot alter a historical report. The
    input is required to be monotonic and unambiguous; this is not a
    revision-tape reconciliation protocol.
    """
    if not isinstance(instrument_id, str) or not _INSTRUMENT.fullmatch(instrument_id):
        raise ValueError("invalid funding instrument")
    decision_at = _utc(as_of)
    if (
        not isinstance(max_age, timedelta)
        or not 0 < max_age.total_seconds() <= 366 * 86400
        or not math.isfinite(max_age.total_seconds())
    ):
        raise ValueError("max_age must be a finite positive bounded timedelta")
    if not isinstance(observations, (list, tuple)) or len(observations) > _MAX_EVENTS:
        raise ValueError("funding history must be a bounded sequence")
    eligible: list[dict[str, str]] = []
    latest: FundingObservation | None = None
    last_time: datetime | None = None
    for item in observations:
        if not isinstance(item, FundingObservation) or item.instrument_id != instrument_id:
            raise ValueError("mixed or invalid funding instrument observations")
        effective = _utc(item.effective_at)
        observed = _utc(item.observed_at)
        if last_time is not None and effective <= last_time:
            raise ValueError("funding observations must have strictly increasing effective time")
        last_time = effective
        if effective <= decision_at and observed <= decision_at:
            eligible.append({
                "effective_at": effective.isoformat(),
                "observed_at": observed.isoformat(),
                "rate": str(item.rate),
                "source_sha256": item.source_sha256,
            })
            latest = item
    receipt = hashlib.sha256(json.dumps(
        {"instrument_id": instrument_id, "observations": eligible},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    if latest is None:
        return FundingResolution(instrument_id, decision_at, None, None, "unavailable", 0, receipt)
    selected_at = _utc(latest.effective_at)
    stale = decision_at - selected_at > max_age
    return FundingResolution(
        instrument_id, decision_at, selected_at,
        None if stale else latest.rate, "stale" if stale else "available",
        len(eligible), receipt,
    )


def funding_cashflow_estimate(
    resolution: FundingResolution,
    *,
    signed_notional: Decimal,
    intervals: int = 1,
) -> Decimal:
    """Research-only sign convention: positive funding costs a long.

    This is *not* a settled funding payment, fill adjustment, or posting.
    Never feed it to actual accounting without independent broker evidence.
    """
    if (
        type(signed_notional) is not Decimal
        or not signed_notional.is_finite()
        or abs(signed_notional) > Decimal("1e30")
    ):
        raise ValueError("signed_notional must be a finite bounded Decimal")
    if type(intervals) is not int or not 1 <= intervals <= 10000:
        raise ValueError("intervals must be an integer between 1 and 10000")
    if resolution.status != "available" or resolution.rate is None:
        raise PermissionError("funding evidence unavailable for cashflow estimate")
    return -signed_notional * resolution.rate * intervals
