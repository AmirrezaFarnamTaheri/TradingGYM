from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, Optional, List
import logging

import numpy as np
import pandas as pd
from pathlib import Path

from trading_gym.core.config import AppConfig
from trading_gym.data.data_loader import load_symbol_timeframe
from trading_gym.data.features.feature_fusion import fuse_features, preserve_base_context
from trading_gym.rl.pipelines.model_bundle import FrozenNormalizer
from trading_gym.core.timeframes import timeframe_seconds

if TYPE_CHECKING:
    from trading_gym.rl.margin_trading_environment import MarginTradingEnv

logger = logging.getLogger(__name__)


def _timeframe_candidates(
    tf_name: str, bar_minutes: int, override: Optional[str]
) -> List[str]:
    """Generate plausible timeframe labels to look for on disk."""
    candidates: List[str] = []
    if override:
        candidates.append(override)

    # If tf_name already looks like a timeframe (e.g., "5m" or "1h"), try it first
    if any(tf_name.endswith(suffix) for suffix in ("m", "h", "d")):
        candidates.append(tf_name)

    # Derive from bar minutes
    if bar_minutes % 1440 == 0:
        day = bar_minutes // 1440
        candidates.append(f"{day}d")
    if bar_minutes % 60 == 0:
        hour = bar_minutes // 60
        candidates.append(f"{hour}h")
    candidates.append(f"{bar_minutes}m")

    # Remove duplicates while preserving order
    seen = set()
    ordered = []
    for cand in candidates:
        if cand not in seen:
            ordered.append(cand)
            seen.add(cand)
    return ordered


def _compute_stats_from_features(
    features_df: pd.DataFrame, feature_cfg
) -> Dict[str, Dict[str, float]]:
    """Fallback computation of normalization stats when none are provided."""
    return FrozenNormalizer.fit(
        features_df, feature_cfg.standardization.method
    ).to_stats()


def load_environment_inputs(
    cfg: AppConfig,
    timeframe: str,
    *,
    start_at: pd.Timestamp | str | None = None,
    end_at: pd.Timestamp | str | None = None,
) -> Dict[str, np.ndarray | pd.DataFrame]:
    """Load complete closed-bar features under the shared replay/live recipe.

    The optional half-open bounds apply to decision/availability timestamps.
    Earlier raw history supplies causal rolling context but no fit statistics.
    Missing declared partitions and internal stale gaps fail closed.
    """
    from trading_gym.data.features.feature_assembler import FeatureAssembler
    from trading_gym.rl.pipelines.availability import (
        FEATURE_RECIPE,
        SETTLEMENT_DELAY_SECONDS,
        active_timeframes,
        align_available_frame,
        available_index,
        closed_feature_frame,
        feature_history_rows,
        utc_bar_frame,
    )

    assembler = FeatureAssembler(cfg.features)
    timeframes = active_timeframes(cfg.features)
    base_seconds = timeframes[0].bar_minutes * 60
    if timeframe_seconds(timeframe) != base_seconds:
        raise ValueError(
            "requested timeframe differs from configured base feature duration"
        )
    frames = {}
    base_raw = None
    for index, tf_cfg in enumerate(timeframes):
        candidates = _timeframe_candidates(
            tf_cfg.name, tf_cfg.bar_minutes, timeframe if index == 0 else None
        )
        raw = None
        for candidate in candidates:
            try:
                raw = load_symbol_timeframe(
                    Path(cfg.data.lake_dir), cfg.env.symbol, candidate
                )
                break
            except FileNotFoundError:
                continue
        if raw is None:
            raise FileNotFoundError(
                f"missing required timeframe {tf_cfg.name!r}; tried {candidates}"
            )
        raw = utc_bar_frame(raw)
        for column in ("open", "high", "low", "mid"):
            if column not in raw:
                raw[column] = raw["close"]
        frames[tf_cfg.name] = closed_feature_frame(
            raw, assembler, cfg.env.symbol, bar_seconds=tf_cfg.bar_minutes * 60
        )
        if index == 0:
            base_raw = raw.copy()
            base_raw["bar_open_timestamp"] = raw.index
            base_raw.index = available_index(raw.index, base_seconds)

    decisions = frames[timeframes[0].name].index
    # Warm-up rows before any required frame has a complete causal history are
    # ineligible; internal missing publications are not silently zero-filled.
    first_complete = max(frame.index[0] for frame in frames.values())
    decisions = decisions[decisions >= first_complete]

    def bound(value):
        if value is None:
            return None
        timestamp = pd.Timestamp(value)
        return (
            timestamp.tz_localize("UTC")
            if timestamp.tzinfo is None
            else timestamp.tz_convert("UTC")
        )

    start, end = bound(start_at), bound(end_at)
    if start is not None and end is not None and start >= end:
        raise ValueError("environment start_at must precede end_at")
    if start is not None:
        decisions = decisions[decisions >= start]
    if end is not None:
        decisions = decisions[decisions < end]
    if decisions.empty:
        raise ValueError("environment time window contains no complete admitted rows")
    ordered = []
    for tf_cfg in timeframes:
        # A higher timeframe may carry until its next expected publication.
        # roll_fill_cap grants that many EXTRA base bars during a source outage;
        # zero therefore permits no extra outage fill, not no normal as-of reuse.
        maximum_age = tf_cfg.bar_minutes * 60 + tf_cfg.roll_fill_cap * base_seconds
        aligned = align_available_frame(
            frames[tf_cfg.name], decisions, max_age_seconds=maximum_age
        )
        if ordered and tuple(aligned.columns) != tuple(ordered[0].columns):
            raise ValueError("feature schemas differ across timeframes")
        ordered.append(aligned)
    if len(ordered) > 1:
        values = np.stack([frame.to_numpy(dtype=np.float32) for frame in ordered])
        fused = fuse_features(values, np.zeros(values.shape[:2], dtype=bool))
        fused = preserve_base_context(fused, values[0], ordered[0].columns)
        features = pd.DataFrame(fused, index=decisions, columns=ordered[0].columns)
    else:
        features = ordered[0]
    market = base_raw.reindex(decisions)
    if market[["mid", "high", "low"]].isna().any().any():
        raise ValueError("base market data is not aligned to decision timestamps")
    return {
        "features": features,
        "prices_mid": market["mid"].to_numpy(),
        "prices_high": market["high"].to_numpy(),
        "prices_low": market["low"].to_numpy(),
        "timestamps": decisions.asi8.astype(np.float64) / 1e9,
        "bar_open_timestamps": pd.DatetimeIndex(market["bar_open_timestamp"]),
        "ohlc": market[["open", "high", "low", "close"]].copy(),
        "feature_provenance": {
            "recipe": FEATURE_RECIPE,
            "bar_timestamp_semantics": "open",
            "settlement_delay_seconds": SETTLEMENT_DELAY_SECONDS,
            "history_rows": feature_history_rows(cfg.features),
            "timeframes": [tf.name for tf in timeframes],
            "missingness": "complete-asof-fail-stale",
        },
    }


def create_environment(
    cfg: AppConfig,
    inputs: Dict[str, np.ndarray | pd.DataFrame],
    live_mode: bool = False,
    training_stats: Optional[Dict[str, float]] = None,
) -> "MarginTradingEnv":
    """Create a MarginTradingEnv instance from loaded data."""
    if training_stats is None:
        raise ValueError(
            "frozen training_stats required; fit on designated training rows or load an admitted model bundle"
        )
    normalizer = FrozenNormalizer.from_stats(inputs["features"].columns, training_stats)
    normalizer.validate_features(inputs["features"])
    stats = normalizer.to_stats()
    from trading_gym.rl.margin_trading_environment import MarginTradingEnv

    env = MarginTradingEnv(
        features=inputs["features"],
        prices_mid=inputs["prices_mid"],
        prices_high=inputs["prices_high"],
        prices_low=inputs["prices_low"],
        cfg=cfg.env,
        features_cfg=cfg.features,
        initial_equity=cfg.training.initial_equity,
        live_mode=live_mode,
        training_stats=stats,
        timestamps=inputs.get("timestamps"),
        bar_seconds=cfg.features.timeframes[0].bar_minutes * 60,
        max_steps=cfg.simulator.steps,
    )
    return env


def build_env_factory(
    cfg: AppConfig, timeframe: str, training_stats: Optional[Dict[str, float]] = None
) -> Callable[[], "MarginTradingEnv"]:
    """
    Build a factory function that creates a MarginTradingEnv instance on demand.
    This is useful for parallelization and for creating separate eval environments.
    """
    inputs = load_environment_inputs(cfg, timeframe)

    def _factory() -> "MarginTradingEnv":
        return create_environment(cfg, inputs, training_stats=training_stats)

    return _factory
