"""One closed-bar feature recipe shared by replay and live observations.

OHLCV indices are bar OPEN times. A feature becomes admissible at bar close
plus the fixed settlement delay. This is a declared data-source convention,
not a claim that every vendor publishes on that schedule.
"""

from __future__ import annotations

import re
import numpy as np
import pandas as pd
from numba import njit

from trading_gym.features.feature_assembler import FeatureAssembler, calculate_rsi
from trading_gym.common.timeframes import timeframe_seconds

SETTLEMENT_DELAY_SECONDS = 2
MAX_FEATURE_HISTORY_ROWS = 997  # allow forming bars within the live 1,000-row cap
FEATURE_RECIPE = "closed-bars-bounded-rsi-fixed-fusion.v3"


def feature_history_rows(feature_cfg) -> int:
    """A fixed window includes every finite rolling dependency and RSI seed."""
    rows = max(64, int(feature_cfg.rolling_window) // 10 + 2)
    if rows > MAX_FEATURE_HISTORY_ROWS:
        raise ValueError(
            "feature history exceeds the shared replay/live capacity; choose a supported recipe"
        )
    return rows


def utc_bar_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("bar-open timestamps must be timezone-aware")
    if (
        frame.empty
        or not frame.index.is_unique
        or not frame.index.is_monotonic_increasing
    ):
        raise ValueError("bar-open timestamps must be nonempty, unique and ordered")
    result = frame.copy()
    result.index = result.index.tz_convert("UTC")
    return result


def available_index(opens: pd.DatetimeIndex, bar_seconds: float) -> pd.DatetimeIndex:
    if not np.isfinite(bar_seconds) or bar_seconds <= 0:
        raise ValueError("bar duration must be finite and positive")
    if opens.tz is None or not opens.is_unique or not opens.is_monotonic_increasing:
        raise ValueError("availability requires ordered timezone-aware bar opens")
    return opens.tz_convert("UTC") + pd.to_timedelta(
        bar_seconds + SETTLEMENT_DELAY_SECONDS, unit="s"
    )


@njit(cache=True)
def _bounded_rsi(prices: np.ndarray, history: int) -> np.ndarray:
    # Reuse the native indicator, but use the identical finite seed window at
    # each decision. This avoids offline full-history/live short-history drift.
    output = np.empty(len(prices) - history + 1, dtype=np.float32)
    for end in range(history, len(prices) + 1):
        output[end - history] = (
            calculate_rsi(prices[end - history : end], 14)[-1] / 100.0
        )
    return output


def closed_feature_frame(
    frame: pd.DataFrame, assembler: FeatureAssembler, symbol: str, *, bar_seconds: float
) -> pd.DataFrame:
    raw = utc_bar_frame(frame)
    history = feature_history_rows(assembler.cfg)
    if len(raw) < history:
        raise ValueError(f"feature recipe requires {history} completed history rows")
    # Returns/volatility/spread/static metadata are already causal and finite
    # horizon; only Wilder RSI has an unbounded recurrence to freeze explicitly.
    full = assembler.process(raw, symbol)
    features = full.iloc[history - 1 :].copy()
    features["rsi"] = _bounded_rsi(
        np.ascontiguousarray(raw["close"], dtype=np.float64), history
    )
    features.index = available_index(features.index, bar_seconds)
    return features


def align_available_frame(
    frame: pd.DataFrame, decisions: pd.DatetimeIndex, *, max_age_seconds: float
) -> pd.DataFrame:
    """Backward as-of match with a strict freshness bound; never forward-fill future data."""
    if max_age_seconds <= 0 or not np.isfinite(max_age_seconds):
        raise ValueError("freshness bound must be finite and positive")
    if frame.index.tz is None or decisions.tz is None:
        raise ValueError("availability and decisions must be timezone-aware")
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError("availability timestamps must be unique and ordered")
    if not decisions.is_unique or not decisions.is_monotonic_increasing:
        raise ValueError("decision timestamps must be unique and ordered")
    positions = frame.index.searchsorted(decisions, side="right") - 1
    if (positions < 0).any():
        raise ValueError("a required feature is not yet available")
    ages = (decisions.asi8 - frame.index[positions].asi8) / 1e9
    if (ages >= max_age_seconds).any():
        raise ValueError("a required feature exceeded its availability/freshness bound")
    aligned = frame.iloc[positions].copy()
    aligned.index = decisions
    return aligned


def active_timeframes(feature_cfg):
    configured = list(feature_cfg.timeframes)
    if not configured or len({tf.name for tf in configured}) != len(configured):
        raise ValueError("feature timeframes must be nonempty and uniquely named")
    if any(tf.bar_minutes <= 0 or tf.roll_fill_cap < 0 for tf in configured):
        raise ValueError(
            "timeframes require positive durations and nonnegative outage allowances"
        )
    for tf in configured:
        # A recognized interval name selects the actual vendor/lake series. Its
        # duration must agree with the clock used to admit that bar as closed.
        # Display aliases such as tf_5m continue to derive from bar_minutes.
        if (
            re.fullmatch(r"\d+[smhdw]", tf.name)
            and timeframe_seconds(tf.name) != tf.bar_minutes * 60
        ):
            raise ValueError("timeframe interval name and bar_minutes disagree")
    return configured if feature_cfg.attention.enabled else configured[:1]
