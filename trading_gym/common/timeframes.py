"""Explicit elapsed-time and annualization units shared by research and runtime."""
from __future__ import annotations

import math
import re

_TIMEFRAME = re.compile(r"([1-9][0-9]*)([smhdw])\Z")
_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_seconds(value: str) -> float:
    match = _TIMEFRAME.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("timeframe must be a positive integer followed by s, m, h, d or w")
    try:
        seconds = float(int(match[1]) * _SECONDS[match[2]])
    except (ValueError, OverflowError) as exc:
        raise ValueError("timeframe is out of range") from exc
    if not math.isfinite(seconds):
        raise ValueError("timeframe must have a finite duration")
    return seconds


def periods_per_year(value: str, *, calendar_days: float = 365.0) -> float:
    if not math.isfinite(calendar_days) or calendar_days <= 0:
        raise ValueError("calendar_days must be finite and positive")
    return calendar_days * 86400.0 / timeframe_seconds(value)
