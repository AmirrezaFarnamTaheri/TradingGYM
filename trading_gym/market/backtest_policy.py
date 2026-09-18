"""Market-aware backtest workload and provider-history limits."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}


@dataclass(frozen=True)
class BacktestPolicy:
    max_days: int
    max_bars: int
    reason: str


_DEFAULT_DAYS = {
    "1m": 30,
    "3m": 60,
    "5m": 180,
    "15m": 365,
    "30m": 365,
    "1h": 1095,
    "4h": 1825,
    "1d": 3650,
    "1w": 7300,
}

_MARKET_OVERRIDES = {
    "equity": {"1m": 7, "3m": 7, "5m": 60, "15m": 60, "30m": 60, "1h": 700},
    "forex": {"1m": 7, "3m": 30, "5m": 60, "15m": 60, "30m": 120, "1h": 365},
}


class BacktestLimitError(ValueError):
    def __init__(self, details: dict[str, object]) -> None:
        self.details = details
        super().__init__(str(details.get("reason", "backtest request exceeds policy")))


def normalise_timeframe(value: str) -> str:
    timeframe = value.strip().lower()
    if timeframe not in _TIMEFRAME_SECONDS:
        raise ValueError(f"unsupported timeframe: {value}")
    return timeframe


def warmup_calendar_days(timeframe: str, warmup_bars: int) -> int:
    tf = normalise_timeframe(timeframe)
    bars = max(0, int(warmup_bars))
    if bars == 0:
        return 0
    seconds = _TIMEFRAME_SECONDS[tf]
    calendar_days = bars * seconds / 86400
    if tf.endswith("d"):
        calendar_days *= 1.9  # weekends/holidays and a conservative buffer
    elif tf.endswith("w"):
        calendar_days *= 1.2
    else:
        calendar_days *= 1.5
    return max(1, math.ceil(calendar_days))


def policy_for(
    *, market: str, timeframe: str, absolute_max_bars: int = 2_000_000
) -> BacktestPolicy:
    tf = normalise_timeframe(timeframe)
    market_key = market.strip().lower()
    max_days = _MARKET_OVERRIDES.get(market_key, {}).get(tf, _DEFAULT_DAYS[tf])
    bars = max(1, (max_days * 86400) // _TIMEFRAME_SECONDS[tf])
    return BacktestPolicy(
        max_days=max_days,
        max_bars=min(bars, absolute_max_bars),
        reason=f"{market_key or 'default'} {tf} workload/provider limit",
    )


def validate_backtest_window(
    *,
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    warmup_bars: int = 0,
) -> dict[str, object]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("backtest timestamps must be timezone-aware")
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    if end_utc <= start_utc:
        raise ValueError("backtest end must be after start")
    policy = policy_for(market=market, timeframe=timeframe)
    warmup_days = warmup_calendar_days(timeframe, warmup_bars)
    fetch_start = start_utc - timedelta(days=warmup_days)
    requested_days = max(1, math.ceil((end_utc - fetch_start).total_seconds() / 86400))
    estimated_bars = math.ceil(
        (end_utc - fetch_start).total_seconds()
        / _TIMEFRAME_SECONDS[normalise_timeframe(timeframe)]
    )
    details: dict[str, object] = {
        **asdict(policy),
        "market": market.strip().lower(),
        "timeframe": normalise_timeframe(timeframe),
        "start": start_utc.isoformat(),
        "end": end_utc.isoformat(),
        "fetch_start": fetch_start.isoformat(),
        "warmup_bars": warmup_bars,
        "warmup_days": warmup_days,
        "requested_days": requested_days,
        "estimated_bars": estimated_bars,
    }
    if requested_days > policy.max_days or estimated_bars > policy.max_bars:
        details["allowed"] = False
        details["latest_allowed_start"] = (
            end_utc - timedelta(days=policy.max_days - 1)
        ).isoformat()
        raise BacktestLimitError(details)
    details["allowed"] = True
    return details
