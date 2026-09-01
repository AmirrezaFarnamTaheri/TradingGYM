from __future__ import annotations

from typing import Callable, Dict, Optional, List
import logging

import numpy as np
import pandas as pd
from pathlib import Path

from trading_gym.common.config import AppConfig
from trading_gym.data.data_loader import load_symbol_timeframe
from trading_gym.features.feature_assembler import FeatureAssembler
from trading_gym.features.feature_fusion import fuse_features
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
    numeric_cols = features_df.select_dtypes(include=[np.number])
    method = feature_cfg.standardization.method.lower()
    if method == "robust":
        center = numeric_cols.median()
        scale = numeric_cols.quantile(0.75) - numeric_cols.quantile(0.25)
    else:
        center = numeric_cols.mean()
        scale = numeric_cols.std()

    scale = scale.replace(0, 1.0)
    return {"method": method, "center": center.to_dict(), "scale": scale.to_dict()}


def load_environment_inputs(
    cfg: AppConfig,
    timeframe: str,
    *,
    start_at: pd.Timestamp | str | None = None,
    end_at: pd.Timestamp | str | None = None,
) -> Dict[str, np.ndarray | pd.DataFrame]:
    """Load aligned environment inputs with optional half-open UTC bounds.

    Features are calculated from the complete admitted history first so rolling
    indicators retain their warm-up context. The final aligned base index is then
    sliced to ``[start_at, end_at)`` across every feature and price tensor.
    """
    assembler = FeatureAssembler(cfg.features)
    feature_frames: Dict[str, pd.DataFrame] = {}
    base_raw: Optional[pd.DataFrame] = None
    base_tf_name: Optional[str] = None

    for idx, tf_cfg in enumerate(cfg.features.timeframes):
        candidates = _timeframe_candidates(
            tf_cfg.name, tf_cfg.bar_minutes, timeframe if idx == 0 else None
        )
        raw_data = None
        errors: list[str] = []
        for cand in candidates:
            try:
                raw_data = load_symbol_timeframe(
                    lake_dir=Path(cfg.data.lake_dir),
                    symbol=cfg.env.symbol,
                    timeframe=cand,
                )
                break
            except FileNotFoundError as exc:
                errors.append(str(exc))
                continue

        if raw_data is None:
            detail = "; ".join(errors) if errors else "No data candidates found."
            msg = (
                f"Missing data for timeframe '{tf_cfg.name}' ({candidates}) - {detail}"
            )
            if idx == 0:
                raise FileNotFoundError(msg)
            logger.warning(msg)
            continue

        # Add high/low if they don't exist for compatibility with older data
        if "high" not in raw_data.columns:
            raw_data["high"] = raw_data["close"]
        if "low" not in raw_data.columns:
            raw_data["low"] = raw_data["close"]

        # Ensure 'mid' price is available for the environment
        if "mid" not in raw_data.columns:
            raw_data["mid"] = raw_data["close"]

        feature_frames[tf_cfg.name] = assembler.process(raw_data, cfg.env.symbol)

        if base_raw is None:
            base_raw = raw_data
            base_tf_name = tf_cfg.name

    if base_raw is None or base_tf_name is None:
        raise ValueError(
            "No feature frames were generated; check feature configuration."
        )

    base_index = feature_frames[base_tf_name].index
    if not isinstance(base_index, pd.DatetimeIndex):
        raise ValueError("base feature frame must use a DatetimeIndex")
    if base_index.tz is None:
        base_index = base_index.tz_localize("UTC")
    else:
        base_index = base_index.tz_convert("UTC")

    def normalise_bound(value: pd.Timestamp | str | None) -> pd.Timestamp | None:
        if value is None:
            return None
        bound = pd.Timestamp(value)
        if bound.tzinfo is None:
            return bound.tz_localize("UTC")
        return bound.tz_convert("UTC")

    start_bound = normalise_bound(start_at)
    end_bound = normalise_bound(end_at)
    if start_bound is not None and end_bound is not None and start_bound >= end_bound:
        raise ValueError("environment start_at must precede end_at")
    selected = np.ones(len(base_index), dtype=bool)
    if start_bound is not None:
        selected &= base_index >= start_bound
    if end_bound is not None:
        selected &= base_index < end_bound
    base_index = base_index[selected]
    if base_index.empty:
        raise ValueError("environment time window contains no admitted rows")

    base_raw_aligned = base_raw.copy()
    if not isinstance(base_raw_aligned.index, pd.DatetimeIndex):
        raise ValueError("base market data must use a DatetimeIndex")
    if base_raw_aligned.index.tz is None:
        base_raw_aligned.index = base_raw_aligned.index.tz_localize("UTC")
    else:
        base_raw_aligned.index = base_raw_aligned.index.tz_convert("UTC")
    base_raw_aligned = base_raw_aligned.reindex(base_index)
    if base_raw_aligned[["mid", "high", "low"]].isna().any().any():
        raise ValueError("base market data does not align with selected feature rows")

    # Align and fuse multi-timeframe features if enabled
    ordered_features: List[pd.DataFrame] = []
    masks: List[np.ndarray] = []
    for tf_cfg in cfg.features.timeframes:
        frame = feature_frames.get(tf_cfg.name)
        if frame is None:
            continue

        aligned = frame.reindex(base_index)
        missing_mask = aligned.isna().any(axis=1).to_numpy()
        ffill_limit = tf_cfg.roll_fill_cap if tf_cfg.roll_fill_cap > 0 else None
        if ffill_limit is not None:
            aligned = aligned.ffill(limit=ffill_limit)
        aligned = aligned.fillna(0.0)
        ordered_features.append(aligned)
        masks.append(missing_mask)

    if not ordered_features:
        raise ValueError("No aligned feature frames available for fusion.")

    if cfg.features.attention.enabled and len(ordered_features) > 1:
        feature_tensor = np.stack(
            [feat.to_numpy(dtype=np.float32) for feat in ordered_features]
        )
        mask_tensor = np.stack(masks)
        fused = fuse_features(feature_tensor, mask_tensor)
        fused_features = pd.DataFrame(
            fused,
            index=base_index,
            columns=feature_frames[base_tf_name].columns,
        )
    else:
        fused_features = ordered_features[0]

    return {
        "features": fused_features,
        "prices_mid": base_raw_aligned["mid"].to_numpy(),
        "prices_high": base_raw_aligned["high"].to_numpy(),
        "prices_low": base_raw_aligned["low"].to_numpy(),
    }


def create_environment(
    cfg: AppConfig,
    inputs: Dict[str, np.ndarray | pd.DataFrame],
    live_mode: bool = False,
    training_stats: Optional[Dict[str, float]] = None,
) -> MarginTradingEnv:
    """Create a MarginTradingEnv instance from loaded data."""
    stats = training_stats
    if stats is None:
        logger.warning(
            "training_stats not provided; computing from supplied features for normalization."
        )
        stats = _compute_stats_from_features(inputs["features"], cfg.features)

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
    )
    # Pass simulator steps to the environment
    env.max_steps = cfg.simulator.steps
    return env


def build_env_factory(
    cfg: AppConfig, timeframe: str, training_stats: Optional[Dict[str, float]] = None
) -> Callable[[], MarginTradingEnv]:
    """
    Build a factory function that creates a MarginTradingEnv instance on demand.
    This is useful for parallelization and for creating separate eval environments.
    """
    inputs = load_environment_inputs(cfg, timeframe)

    def _factory() -> MarginTradingEnv:
        return create_environment(cfg, inputs, training_stats=training_stats)

    return _factory
