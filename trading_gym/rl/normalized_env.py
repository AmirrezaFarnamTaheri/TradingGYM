"""Observation-normalising, reward-clipping wrapper around ``MarginTradingEnv``.

Every behaviour is opt-in through the constructor flags, so the wrapper can be
removed without touching the base environment.
"""

import logging
import numpy as np
import gymnasium as gym
from typing import Any, Dict, Optional, Tuple, Union

logger = logging.getLogger(__name__)


class NormalizedMarginEnv(gym.Wrapper):
    """Normalise observations, clip rewards, and optionally extend the state space.

    Training statistics must be supplied out-of-band (``training_stats``) for
    offline training; computing them from the episode under test leaks the
    future into the observation.

    Example:
        >>> env = NormalizedMarginEnv(MarginTradingEnv(...))
        >>> obs, info = env.reset()
    """

    def __init__(
        self,
        env: gym.Env,
        reward_clip: float = 0.10,
        enable_normalization: bool = True,
        enable_enhanced_obs: bool = True,
        enable_action_feedback: bool = True,
        training_stats: Optional[Dict[str, float]] = None,
    ):
        """Initialise the wrapper.

        ``training_stats`` carries pre-computed normalisation statistics so that
        offline training never derives them from the episode it is scoring.
        """
        super().__init__(env)

        self.reward_clip = reward_clip
        self.enable_normalization = enable_normalization
        self.enable_enhanced_obs = enable_enhanced_obs
        self.enable_action_feedback = enable_action_feedback
        self.training_stats = training_stats

        # Calculate normalization statistics from data
        if self.enable_normalization:
            if self.training_stats:
                self._compute_normalization_stats_from_dict(self.training_stats)
            else:
                logger.warning(
                    "Normalisation is enabled without training_stats; deriving them "
                    "from the live episode is safe only in live mode."
                )
                base_env = self.env.unwrapped
                price_arr = np.asarray(getattr(base_env, "price", []), dtype=np.float64)
                # Fallback to computing on the fly, NOT recommended for training
                self._compute_normalization_stats(price_arr, np.array([]), np.array([]))

        # Update observation space if using enhanced features
        if self.enable_enhanced_obs:
            # 12 features instead of 5
            obs_high = np.full(12, np.inf, dtype=np.float64)
            obs_low = -obs_high
            self.observation_space = gym.spaces.Box(
                low=obs_low, high=obs_high, dtype=np.float64
            )

        # Track statistics for logging
        self.episode_rewards: list[float] = []
        self.episode_returns: list[float] = []
        self.action_clips = 0
        self.total_steps = 0

        logger.info(
            "NormalizedMarginEnv initialized: "
            f"reward_clip={reward_clip}, "
            f"normalization={enable_normalization}, "
            f"enhanced_obs={enable_enhanced_obs}, "
            f"action_feedback={enable_action_feedback}"
        )

    def _compute_normalization_stats(
        self, prices: np.ndarray, spreads: np.ndarray, vols: np.ndarray
    ) -> None:
        """Computes normalization stats from raw data arrays."""
        if prices.size == 0:
            price_mean, price_std = 1.0, 1.0
        else:
            price_mean, price_std = np.mean(prices), np.std(prices)

        if spreads.size == 0:
            spread_mean, spread_std = 0.0, 1.0
        else:
            spread_mean, spread_std = np.mean(spreads), np.std(spreads)

        if vols.size == 0:
            vol_mean, vol_std = 0.0, 1.0
        else:
            vol_mean, vol_std = np.mean(vols), np.std(vols)

        # Create a stats dict and reuse the dict method
        stats = {
            "price_mean": float(price_mean),
            "price_std": float(price_std),
            "spread_mean": float(spread_mean),
            "spread_std": float(spread_std),
            "vol_mean": float(vol_mean),
            "vol_std": float(vol_std),
        }
        self._compute_normalization_stats_from_dict(stats)

    def _compute_normalization_stats_from_dict(self, stats: Dict[str, float]) -> None:
        """Computes normalization stats from a dictionary."""
        price_mean = stats.get("price_mean", 1.0)
        price_std = stats.get("price_std", 1.0)
        spread_mean = stats.get("spread_mean", 0.0)
        spread_std = stats.get("spread_std", 1.0)
        vol_mean = stats.get("vol_mean", 0.0)
        vol_std = stats.get("vol_std", 1.0)
        base_env = self.env.unwrapped
        safe_price_mean = price_mean if abs(price_mean) > 1e-8 else 1.0

        init_eq = float(getattr(base_env, "initial_equity", 1000.0))

        position_std = (init_eq / safe_price_mean) + 1e-8
        equity_std = abs(init_eq) * 0.1 + 1e-8  # 10% moves

        if self.enable_enhanced_obs:
            # Enhanced features normalization (12 total)
            self.obs_mean = np.array(
                [
                    price_mean,  # 0: Price
                    spread_mean,  # 1: Spread BPS
                    vol_mean,  # 2: Volatility
                    0.0,  # 3: Position
                    0.5,  # 4: Leverage utilization (0-1)
                    init_eq,  # 5: Equity
                    5.0,  # 6: Margin ratio (typical value)
                    0.0,  # 7: Unrealized PnL %
                    0.5,  # 8: Time progress (0-1)
                    0.0,  # 9: Price momentum
                    0.8,  # 10: Liquidity available (0-1)
                    0.05,  # 11: Data quality (missing data rate)
                ],
                dtype=np.float64,
            )

            self.obs_std = np.array(
                [
                    price_std,  # 0: Price
                    spread_std,  # 1: Spread BPS
                    vol_std,  # 2: Volatility
                    position_std,  # 3: Position
                    0.3,  # 4: Leverage utilization
                    equity_std,  # 5: Equity
                    3.0,  # 6: Margin ratio
                    0.1,  # 7: Unrealized PnL %
                    0.3,  # 8: Time progress
                    0.05,  # 9: Price momentum
                    0.3,  # 10: Liquidity available
                    0.1,  # 11: Data quality
                ],
                dtype=np.float64,
            )
        else:
            # Basic features only (5)
            self.obs_mean = np.array(
                [
                    price_mean,
                    spread_mean,
                    vol_mean,
                    0.0,
                    init_eq,
                ],
                dtype=np.float64,
            )

            self.obs_std = np.array(
                [
                    price_std,
                    spread_std,
                    vol_std,
                    position_std,
                    equity_std,
                ],
                dtype=np.float64,
            )

        logger.info(
            f"Normalization stats computed: "
            f"price μ={price_mean:.2f} σ={price_std:.2f}, "
            f"spread μ={spread_mean:.2f} σ={spread_std:.2f}, "
            f"vol μ={vol_mean:.4f} σ={vol_std:.4f}"
        )

    def _get_enhanced_obs(self) -> np.ndarray:
        """Get enhanced observation with 12 features and safe array access."""
        base_env = self.env.unwrapped

        # Safely derive sizes and current index
        price_arr = np.asarray(getattr(base_env, "price", []), dtype=np.float64)
        # Spread/volatility come from the base env's raw feature frame and its
        # feature_index, exactly as margin_trading_environment reads them.
        # The base env does not expose spread_bps/vol/buy_cap/sell_cap/missing
        # attributes; reading them via getattr defaults silently produced
        # constants, padding the observation with non-information.
        feature_index = getattr(base_env, "feature_index", {})
        # The current raw row comes from the base env's own accessor: a freshly
        # streamed live bar lands in _raw_buffer, not the historical
        # _raw_features frame, so indexing that frame returned a stale row in
        # live mode.
        if getattr(base_env, "live_mode", False) and hasattr(
            base_env, "_get_current_raw_features"
        ):
            row = np.asarray(base_env._get_current_raw_features(), dtype=np.float64)
        else:
            t_now = int(getattr(base_env, "t", 0) or 0)
            raw = getattr(base_env, "_raw_features", None)
            if raw is not None and t_now < len(raw):
                row = np.asarray(raw[t_now], dtype=np.float64)
            else:
                row = np.empty(0, dtype=np.float64)

        def _feat(name: str) -> float:
            idx = feature_index.get(name)
            if idx is None or idx >= row.size:
                return 0.0
            return float(row[idx])

        # Scalars, not one-element arrays: these are per-bar values for the
        # current bar, and wrapping one only to index it with t again hit the
        # default for every t > 0, padding the observation with constant zeros.
        spread_bps = _feat("spread_ratio") * 10000.0
        volatility = _feat("volatility")
        buy_cap = _feat("buy_capacity")
        sell_cap = _feat("sell_capacity")
        missing_rate = _feat("missing_rate")

        T = int(getattr(base_env, "T", len(price_arr)) or len(price_arr))
        t = int(getattr(base_env, "t", 0) or 0)
        idx = int(np.clip(t, 0, max(T - 1, 0)))

        # Current price with fallback
        current_mid = float(price_arr[idx]) if price_arr.size > idx else 1.0

        # Safe state access
        state = getattr(base_env, "_state", None)

        # Calculate margin ratio
        borrowed_quote = abs(getattr(state, "borrowed_quote", 0.0))
        borrowed_base = abs(getattr(state, "borrowed_base", 0.0))
        equity = float(getattr(state, "equity", 0.0)) if state else 0.0

        # ExecutionState carries Decimal money fields; coerce before mixing
        # with the float mid price, else Decimal * float raises TypeError.
        borrowed_val = float(borrowed_quote) + float(borrowed_base) * current_mid

        if borrowed_val <= 1e-12:
            margin_ratio = 10.0
        else:
            margin_ratio = min(max(equity, 0.0) / borrowed_val, 10.0)

        # Calculate leverage utilization
        position = float(getattr(state, "position", 0.0)) if state else 0.0
        exposure = abs(position * current_mid)
        # The base env exposes per-side limits (_long/_short_max_leverage),
        # not a single _max_leverage; fall back to 0 only if neither exists.
        max_lev = float(
            getattr(base_env, "_long_max_leverage", None)
            or getattr(base_env, "_max_leverage", None)
            or 0.0
        )
        max_equity = max(equity, 1e-8)
        max_exposure = max_lev * max_equity
        leverage_util = (exposure / max_exposure) if max_exposure > 0 else 0.0

        # Calculate time progress
        time_progress = (t / max(T, 1)) if T else 0.0

        # Calculate price momentum (10-bar) with safe division
        lookback = 10
        if price_arr.size and t >= lookback:
            denom = float(price_arr[idx - lookback])
            if np.isfinite(denom) and abs(denom) > 1e-12:
                price_momentum = float((price_arr[idx] - denom) / denom)
            else:
                price_momentum = 0.0
        else:
            price_momentum = 0.0

        # Calculate unrealized PnL %
        if position != 0.0:
            entry_price = float(getattr(state, "average_entry_price", current_mid))
            unrealized_pnl = position * (current_mid - entry_price)
            unrealized_pnl_pct = unrealized_pnl / max(equity, 1e-8)
        else:
            unrealized_pnl_pct = 0.0

        # Liquidity available and data quality are per-bar scalars too.
        def safe_get(arr, index, default):
            return float(arr[index]) if arr.size > index else default

        liquidity_available = 1.0 if (buy_cap > 0 and sell_cap > 0) else 0.0
        data_quality = missing_rate

        # Construct enhanced observation with safe values
        obs = np.array(
            [
                safe_get(price_arr, idx, current_mid),  # 0: Price
                spread_bps,  # 1: Spread BPS
                volatility,  # 2: Volatility
                position,  # 3: Position
                leverage_util,  # 4: Leverage utilization
                equity,  # 5: Equity
                margin_ratio,  # 6: Margin ratio
                unrealized_pnl_pct,  # 7: Unrealized PnL %
                time_progress,  # 8: Time progress
                price_momentum,  # 9: Price momentum
                liquidity_available,  # 10: Liquidity available
                data_quality,  # 11: Data quality
            ],
            dtype=np.float64,
        )

        return obs

    def _normalize_obs(self, obs: np.ndarray) -> np.ndarray:
        """Normalize observation using precomputed statistics."""
        if not self.enable_normalization:
            return obs

        # Z-score normalization: (x - μ) / σ
        normalized = (obs - self.obs_mean) / (self.obs_std + 1e-8)

        # Clip to reasonable bounds to handle outliers
        normalized = np.clip(normalized, -10.0, 10.0)

        return normalized

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Union[str, float]]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Reset the environment and return normalized observation."""
        obs, info = self.env.reset(seed=seed, options=options)

        # Get enhanced observation if enabled
        if self.enable_enhanced_obs:
            obs = self._get_enhanced_obs()

        # Normalize
        obs = self._normalize_obs(obs)

        # Reset episode statistics
        self.episode_rewards = []
        self.episode_returns = []
        self.action_clips = 0

        logger.info(
            f"Environment reset at step {self.total_steps}: "
            f"obs_shape={obs.shape}, obs_range=[{obs.min():.2f}, {obs.max():.2f}]"
        )

        return obs, info

    def step(
        self, action: Union[np.ndarray, float]
    ) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """
        Take a step with bounded rewards and action feedback.

        Enhancements:
        1. Bounds reward to [-reward_clip, +reward_clip]
        2. Adds penalty if action was clipped
        3. Logs action clipping events
        4. Returns enhanced normalized observation
        """
        base_env = self.env.unwrapped

        # Convert action to float
        if isinstance(action, np.ndarray):
            action_value = float(action.item()) if action.size > 0 else 0.0
        else:
            action_value = float(action)

        # Track original action before clipping
        original_action = action_value

        # Take step in base environment
        obs, reward, terminated, truncated, info = self.env.step(action)

        # Get the actual position after execution
        actual_position = float(getattr(base_env._state, "position", 0.0))

        # Check if action was clipped (compare requested vs actual)
        # Account for leverage limits and liquidity constraints
        # Use safe reference price to avoid index underflow
        t = int(getattr(base_env, "t", 0) or 0)
        price_arr = np.asarray(getattr(base_env, "price", []), dtype=np.float64)

        if t <= 0:
            ref_idx = 0
        else:
            ref_idx = min(t - 1, len(price_arr) - 1)

        ref_price = (
            float(price_arr[ref_idx]) if price_arr.size > ref_idx else float("nan")
        )
        if not np.isfinite(ref_price) or ref_price <= 1e-12:
            # Fallback to current price or minimal floor
            ref_price = float(max(getattr(base_env, "prev_mid_price", 0.0), 1e-6))

        # Per-side limits on the real env; equity is Decimal and must be
        # coerced before the float multiply, else float * Decimal raises.
        # The ceiling follows the requested sign, mirroring the base env's own
        # _clip_to_leverage: a short target is bounded by the short cap, not
        # the long cap applied to both sides.
        long_lev = float(
            getattr(base_env, "_long_max_leverage", None)
            or getattr(base_env, "_max_leverage", None)
            or 1.0
        )
        short_lev = float(getattr(base_env, "_short_max_leverage", None) or long_lev)
        equity = float(getattr(base_env._state, "equity", 0.0))
        ceiling_lev = long_lev if original_action >= 0 else short_lev
        max_exposure = ceiling_lev * max(equity, 0.0)
        max_units = max_exposure / max(ref_price, 1e-6)
        clipped_target = float(np.clip(original_action, -max_units, max_units))

        was_clipped = abs(original_action - clipped_target) > 1e-6

        # Apply action clipping feedback
        clipping_penalty = 0.0
        if was_clipped and self.enable_action_feedback:
            # Small penalty for hitting constraints
            clipping_penalty = -0.01
            self.action_clips += 1

            if self.total_steps % 100 == 0:  # Log periodically
                logger.debug(
                    f"Action clipped at step {self.total_steps}: "
                    f"requested={original_action:.2f}, "
                    f"max_allowed={clipped_target:.2f}, "
                    f"actual={actual_position:.2f}"
                )

        # Sanitize reward to handle NaN/Inf before clipping
        original_reward = float(reward)

        if not np.isfinite(original_reward):
            logger.warning(
                f"Invalid reward detected at step {self.total_steps}: {original_reward}, replacing with 0.0"
            )
            info["invalid_reward_detected"] = True
            original_reward = 0.0

        # Bound reward to prevent instability
        reward = float(np.clip(original_reward, -self.reward_clip, self.reward_clip))

        # Add clipping penalty
        reward += clipping_penalty

        # Track statistics
        self.episode_rewards.append(reward)
        self.episode_returns.append(original_reward)
        self.total_steps += 1

        # Get enhanced observation if enabled
        if self.enable_enhanced_obs:
            obs = self._get_enhanced_obs()

        # Normalize observation
        obs = self._normalize_obs(obs)

        # Add clipping info to info dict
        info["action_clipped"] = was_clipped
        info["requested_position"] = original_action
        info["clipped_position"] = clipped_target
        info["actual_position"] = actual_position
        info["original_reward"] = original_reward
        info["clipped_reward"] = reward - clipping_penalty
        info["clipping_penalty"] = clipping_penalty

        # Log episode completion
        if terminated or truncated:
            episode_return = sum(self.episode_returns)
            episode_length = len(self.episode_rewards)
            clip_rate = self.action_clips / max(episode_length, 1)

            logger.info(
                f"Episode completed at step {self.total_steps}: "
                f"length={episode_length}, "
                f"return={episode_return:.4f}, "
                f"clip_rate={clip_rate:.2%}, "
                f"liquidation={info.get('liquidation', False)}"
            )

        return obs, reward, terminated, truncated, info

    def update_latest_candle(self, candle: Union[Dict[str, Any], float]) -> None:
        """Delegate update_latest_candle to the underlying environment."""
        if hasattr(self.env, "update_latest_candle"):
            self.env.update_latest_candle(candle)  # type: ignore
        elif hasattr(self.env.unwrapped, "update_latest_candle"):
            self.env.unwrapped.update_latest_candle(candle)
        else:
            logger.warning(
                "Underlying environment does not support update_latest_candle"
            )

    def get_wrapper_stats(self) -> Dict[str, Any]:
        """Get statistics about the wrapper's operation."""
        return {
            "total_steps": self.total_steps,
            "total_clips": self.action_clips,
            "clip_rate": self.action_clips / max(self.total_steps, 1),
            "normalization_enabled": self.enable_normalization,
            "enhanced_obs_enabled": self.enable_enhanced_obs,
            "action_feedback_enabled": self.enable_action_feedback,
            "reward_clip": self.reward_clip,
        }
