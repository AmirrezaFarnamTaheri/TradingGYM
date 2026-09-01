"""Lake-backed playground windows and the regime arm (ADR-031 §1 / ADR-029 §2).

:class:`LakeWindowProvider` is the Phase-2 data-fusion start: playground
windows come from the canonical lake (``data.data_loader``) instead of
inline payloads, so every run's ``window_sha256`` pins a slice of the
authoritative dataset. Slicing is strict: tz-aware bounds, ordered,
minimum depth, and the same OHLCV validation the live path uses.

:class:`~trading_gym.agent.arms.factory.RegimeArmBackend` closes the
fusion loop's regime input: a deterministic trend/chop classifier over
recent returns whose signal carries the regime tag in its rationale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from trading_gym.data.data_loader import load_symbol_timeframe
from trading_gym.strategy_lab.evaluator import validate_market_frame

__all__ = ["LakeWindowProvider"]

_TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

_MIN_BARS = 60


class LakeWindowProvider:
    """Resolve canonical-lake slices as validated playground windows."""

    def __init__(self, lake_dir: Path | str) -> None:
        self._lake_dir = Path(lake_dir)

    def resolve(
        self,
        *,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
        max_bars: int = 200_000,
    ) -> pd.DataFrame:
        """Load one symbol/timeframe partition and slice [start, end]."""

        if timeframe not in _TIMEFRAME_SECONDS:
            raise ValueError(f"unknown timeframe {timeframe!r}")
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        if start_ts.tzinfo is None or end_ts.tzinfo is None:
            raise ValueError("window bounds must be timezone-aware")
        if end_ts <= start_ts:
            raise ValueError("window end must be after start")

        frame = load_symbol_timeframe(self._lake_dir, symbol, timeframe)
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError("lake frame must use a DatetimeIndex")
        if frame.index.tz is None:
            raise ValueError("lake frame index must be timezone-aware")

        window = frame.loc[(frame.index >= start_ts) & (frame.index <= end_ts)]
        if len(window) < _MIN_BARS:
            raise ValueError(
                f"lake window has {len(window)} bars, below the "
                f"{_MIN_BARS}-bar minimum"
            )
        if len(window) > max_bars:
            raise ValueError(
                f"lake window has {len(window)} bars, above the "
                f"{max_bars}-bar limit"
            )
        validate_market_frame(window)
        return window
