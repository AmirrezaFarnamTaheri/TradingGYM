"""Fail-closed live OHLCV refresh for policy observations.

The policy must never act on a ticker-only mutation of a stale historical feature
row.  This module fetches bounded closed candles, validates their chronology and
OHLCV invariants, recomputes the configured features, and returns one immutable
observation row with source provenance.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

import numpy as np
import pandas as pd

from trading_gym.common.config import AppConfig, TimeframeConfig
from trading_gym.features.feature_assembler import FeatureAssembler
from trading_gym.features.feature_fusion import fuse_features

_INTERVAL = re.compile(r"^[1-9][0-9]{0,4}[mhd]$")
MAX_LIVE_CANDLES = 1_000
MAX_TIMEFRAMES = 16


class KlineClient(Protocol):
    def get_klines(
        self,
        symbol: str,
        interval: str,
        start: int | None = None,
        end: int | None = None,
        limit: int = 1000,
    ) -> Sequence[Any]: ...


@dataclass(frozen=True)
class LiveFeatureSnapshot:
    feature_row: np.ndarray
    feature_columns: tuple[str, ...]
    base_candle_timestamp: int
    base_close: float
    base_high: float
    base_low: float
    source_sha256: str
    timeframe_timestamps: dict[str, int]


def _interval(tf: TimeframeConfig) -> str:
    if not 1 <= tf.bar_minutes <= 10_080:
        raise ValueError("live timeframe bar_minutes must be in [1, 10080]")
    if _INTERVAL.fullmatch(tf.name):
        return tf.name
    if tf.bar_minutes % 1440 == 0:
        return f"{tf.bar_minutes // 1440}d"
    if tf.bar_minutes % 60 == 0:
        return f"{tf.bar_minutes // 60}h"
    return f"{tf.bar_minutes}m"


def _timestamp_seconds(value: Any) -> int:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise ValueError("candle timestamp must be finite and positive")
    while numeric > 10_000_000_000:
        numeric /= 1000.0
    return int(numeric)


def _row_values(raw: Any) -> tuple[Any, Any, Any, Any, Any, Any]:
    if isinstance(raw, dict):
        aliases = {
            "timestamp": ("timestamp", "time", "t", "open_time"),
            "open": ("open", "o"),
            "high": ("high", "h"),
            "low": ("low", "l"),
            "close": ("close", "c"),
            "volume": ("volume", "v"),
        }
        values: list[Any] = []
        for label, names in aliases.items():
            found = next((raw[name] for name in names if name in raw), None)
            if found is None:
                raise ValueError(f"candle is missing {label}")
            values.append(found)
        return tuple(values)  # type: ignore[return-value]
    if isinstance(raw, (list, tuple)) and len(raw) >= 6:
        return raw[0], raw[1], raw[2], raw[3], raw[4], raw[5]
    raise ValueError("candle must be an object or a six-value sequence")


def _validated_frame(
    rows: Sequence[Any],
    *,
    bar_seconds: int,
    now: int,
    min_rows: int,
) -> pd.DataFrame:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ValueError("kline response must be a sequence")
    if len(rows) > MAX_LIVE_CANDLES:
        raise ValueError("kline response exceeds the live candle limit")

    parsed: list[dict[str, float | int]] = []
    for raw in rows:
        timestamp, open_, high, low, close, volume = _row_values(raw)
        values = [float(open_), float(high), float(low), float(close), float(volume)]
        if any(not math.isfinite(value) for value in values):
            raise ValueError("candle values must be finite")
        open_f, high_f, low_f, close_f, volume_f = values
        if min(open_f, high_f, low_f, close_f) <= 0 or volume_f < 0:
            raise ValueError("OHLC must be positive and volume non-negative")
        if high_f < max(open_f, close_f) or low_f > min(open_f, close_f) or low_f > high_f:
            raise ValueError("candle violates OHLC envelope invariants")
        parsed.append(
            {
                "timestamp": _timestamp_seconds(timestamp),
                "open": open_f,
                "high": high_f,
                "low": low_f,
                "close": close_f,
                "volume": volume_f,
            }
        )

    parsed.sort(key=lambda item: int(item["timestamp"]))
    timestamps = [int(item["timestamp"]) for item in parsed]
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("candle timestamps must be unique")
    if any(right <= left for left, right in zip(timestamps, timestamps[1:])):
        raise ValueError("candle timestamps must be strictly increasing")

    # Use only fully closed candles. Exchange APIs commonly include the current
    # forming candle, which would leak future/incomplete information into policy input.
    cutoff = now - 2
    completed = [item for item in parsed if int(item["timestamp"]) + bar_seconds <= cutoff]
    if len(completed) < min_rows:
        raise ValueError(
            f"insufficient completed candles: {len(completed)} < {min_rows}"
        )
    completed = completed[-MAX_LIVE_CANDLES:]
    completed_ts = [int(item["timestamp"]) for item in completed]
    if any((right - left) > bar_seconds * 3 for left, right in zip(completed_ts, completed_ts[1:])):
        raise ValueError("live candle history contains an excessive gap")
    latest_age = now - (completed_ts[-1] + bar_seconds)
    if latest_age > max(bar_seconds * 3, 300):
        raise ValueError("latest completed candle is stale")

    frame = pd.DataFrame(completed)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
    frame = frame.set_index("timestamp")
    frame["mid"] = frame["close"]
    return frame


def build_live_feature_snapshot(
    client: KlineClient,
    cfg: AppConfig,
    *,
    now: int | None = None,
) -> LiveFeatureSnapshot:
    """Build one validated feature row from fully closed live candles."""

    current_time = int(now if now is not None else time.time())
    if len(cfg.features.timeframes) == 0:
        raise ValueError("at least one live feature timeframe is required")
    if len(cfg.features.timeframes) > MAX_TIMEFRAMES:
        raise ValueError("live feature timeframe limit exceeded")

    assembler = FeatureAssembler(cfg.features)
    feature_rows: list[np.ndarray] = []
    feature_columns: tuple[str, ...] | None = None
    timestamps: dict[str, int] = {}
    source_frames: dict[str, list[dict[str, Any]]] = {}
    base_values: tuple[int, float, float, float] | None = None
    min_rows = max(20, cfg.features.rolling_window // 10)
    if min_rows > MAX_LIVE_CANDLES:
        raise ValueError("configured rolling window exceeds live history capacity")

    for index, timeframe in enumerate(cfg.features.timeframes):
        interval = _interval(timeframe)
        bar_seconds = timeframe.bar_minutes * 60
        lookback = min(MAX_LIVE_CANDLES, max(min_rows + 20, 64))
        start = max(1, current_time - bar_seconds * (lookback + 3))
        raw_rows = client.get_klines(
            cfg.env.symbol,
            interval,
            start=start,
            end=current_time,
            limit=lookback,
        )
        frame = _validated_frame(
            raw_rows,
            bar_seconds=bar_seconds,
            now=current_time,
            min_rows=min_rows,
        )
        features = assembler.process(frame, cfg.env.symbol)
        columns = tuple(str(column) for column in features.columns)
        if feature_columns is None:
            feature_columns = columns
        elif columns != feature_columns:
            raise ValueError("live feature columns differ across timeframes")
        row = features.iloc[-1].to_numpy(dtype=np.float32)
        if row.ndim != 1 or not np.isfinite(row).all():
            raise ValueError("live feature row must be one-dimensional and finite")
        feature_rows.append(row)
        latest_timestamp = int(frame.index[-1].timestamp())
        timestamps[timeframe.name] = latest_timestamp
        source_frame = frame.reset_index()
        source_frame["timestamp"] = source_frame["timestamp"].map(
            lambda value: int(value.timestamp())
        )
        source_frames[timeframe.name] = source_frame.to_dict(orient="records")
        if index == 0:
            latest = frame.iloc[-1]
            base_values = (
                latest_timestamp,
                float(latest["close"]),
                float(latest["high"]),
                float(latest["low"]),
            )

    if feature_columns is None or base_values is None:
        raise ValueError("live feature snapshot could not be constructed")
    matrix = np.stack(feature_rows).astype(np.float32)
    if cfg.features.attention.enabled and len(feature_rows) > 1:
        fused = fuse_features(
            matrix[:, np.newaxis, :],
            np.zeros((len(feature_rows), 1), dtype=bool),
        )[0]
    else:
        fused = matrix[0]
    if fused.shape != (len(feature_columns),) or not np.isfinite(fused).all():
        raise ValueError("fused live feature row is invalid")

    source_sha256 = hashlib.sha256(
        json.dumps(source_frames, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    timestamp, close, high, low = base_values
    output = np.array(fused, dtype=np.float32, copy=True)
    output.setflags(write=False)
    return LiveFeatureSnapshot(
        feature_row=output,
        feature_columns=feature_columns,
        base_candle_timestamp=timestamp,
        base_close=close,
        base_high=high,
        base_low=low,
        source_sha256=source_sha256,
        timeframe_timestamps=timestamps,
    )
