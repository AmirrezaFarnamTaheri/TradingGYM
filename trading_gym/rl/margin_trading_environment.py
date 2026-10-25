from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union
from collections import deque
from decimal import Decimal
import gymnasium as gym
import numpy as np
import pandas as pd

from trading_gym.core.config import EnvConfig, FeatureConfig
from trading_gym.execution.execution_engine import (
    ExecutionEngine,
    ExecutionState,
    MarketSnapshot,
)
from trading_gym.execution.margin.lot_positions import LotLedger
from trading_gym.execution.margin.stop_out import plan_stop_out
from trading_gym.execution.risk.circuit_breakers import CircuitBreaker
from trading_gym.execution.rebalance_policy import (
    admit_rebalance,
    net_equity_log_reward,
)

logger = logging.getLogger(__name__)

#: Features the trading loop reads by index through ``feature_index``.
#: ``step`` and ``_should_skip_bar`` raise on absence, and the skip path's
#: KeyError is swallowed into a silent bar skip — so a frame missing one of
#: these columns trades nothing and says nothing. Fail at construction.
_REQUIRED_FEATURES = ("spread_ratio", "volatility")


@dataclass(slots=True)
class StepInfo:
    done: bool = False
    liquidation: bool = False
    liquidation_price: Optional[float] = None
    financing_paid: float = 0.0
    crossing_cost: float = 0.0
    slippage_cost: float = 0.0
    fee_cost: float = 0.0
    warning: bool = False
    reason: Optional[str] = None
    equity: float = 0.0
    positions: Dict[str, float] = field(default_factory=dict)
    liquidity_capped: bool = False
    #: Broker-style margin level: equity over gross market exposure at the
    #: current mid (reciprocal of gross leverage). ``None`` when flat.
    margin_level: Optional[float] = None
    #: Graded stop-out cascade events (lot_id, quantity, realized PnL vs mark,
    #: exit price) applied this step. Empty when no shortfall occurred or the
    #: full-liquidation path was taken instead.
    stop_out_events: List[Dict[str, Any]] = field(default_factory=list)
    #: True when a stop-out cascade restored margin without ending episode.
    graded_stop_out: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {slot: getattr(self, slot) for slot in self.__slots__}


class MarginTradingEnv(gym.Env):
    metadata = {"render_modes": ["human"]}

    def __init__(
        self,
        features: pd.DataFrame,
        prices_mid: np.ndarray,
        prices_high: np.ndarray,
        prices_low: np.ndarray,
        cfg: EnvConfig,
        features_cfg: FeatureConfig,
        initial_equity: float = 10_000.0,
        live_mode=False,
        training_stats=None,
        timestamps: Optional[np.ndarray] = None,
        bar_seconds: Optional[float] = None,
        max_steps: Optional[int] = None,
    ) -> None:
        super().__init__()
        if not (
            features.shape[0]
            == prices_mid.shape[0]
            == prices_high.shape[0]
            == prices_low.shape[0]
        ):
            raise ValueError("Input array mismatch.")

        self.live_mode = live_mode
        if not live_mode and training_stats is None:
            raise ValueError("Training stats required for normalization.")

        feature_columns = list(features.columns)
        self.feature_index = {name: i for i, name in enumerate(feature_columns)}
        missing = [
            name for name in _REQUIRED_FEATURES if name not in self.feature_index
        ]
        if missing:
            raise ValueError(
                f"MarginTradingEnv requires the "
                f"{'/'.join(_REQUIRED_FEATURES)} feature columns; missing: "
                f"{', '.join(missing)}. Available: {feature_columns}"
            )
        self._centers, self._scales = self._build_normalization_arrays(
            training_stats, feature_columns
        )
        normalized = self._normalize_features_array(features.to_numpy())
        self.features = self._as_read_only(normalized, np.float32)
        self.feature_df = features
        # Raw (unnormalised) feature rows feed market-microstructure values
        # (spread, volatility). Normalised z-scores are signed and must never
        # be handed to the execution engine as physical quantities.
        self._raw_features = features.to_numpy(dtype=np.float32, copy=True)
        self.price = np.array(prices_mid, dtype=np.float64)
        self.price_high = np.array(prices_high, dtype=np.float64)
        self.price_low = np.array(prices_low, dtype=np.float64)

        # Resolve the shape first: the live-mode buffers size themselves from
        # self.T, so it must exist before they are constructed.
        self.T, self.n_features = self.features.shape
        self.bar_seconds = float(
            bar_seconds
            if bar_seconds is not None
            else features_cfg.timeframes[0].bar_minutes * 60
        )
        if not np.isfinite(self.bar_seconds) or self.bar_seconds <= 0:
            raise ValueError("bar_seconds must be finite and positive")
        if timestamps is None and isinstance(features.index, pd.DatetimeIndex):
            if features.index.hasnans:
                raise ValueError("market timestamps must not contain NaT")
            timestamps = features.index.asi8.astype(np.float64) / 1e9
        self.clock_source = (
            "market_timestamps" if timestamps is not None else "configured_bar_duration"
        )
        self.timestamps = (
            np.asarray(timestamps, dtype=np.float64).copy()
            if timestamps is not None
            else np.arange(self.T, dtype=np.float64) * self.bar_seconds
        )
        if (
            self.timestamps.shape != (self.T,)
            or not np.isfinite(self.timestamps).all()
            or (np.diff(self.timestamps) <= 0).any()
        ):
            raise ValueError(
                "market timestamps must be finite, ordered, unique and aligned"
            )
        self._live_observed_at: Optional[float] = None
        self._account_observation_anchor: Optional[tuple[float, float]] = None
        if self.live_mode:
            # Seed BOTH rolling buffers with the historical window: the raw rows
            # feed microstructure/skip checks (_get_current_raw_features reads
            # _raw_buffer[-1]) just as the normalised rows feed the observation.
            # Leaving _raw_buffer empty meant the first step before a live bar
            # arrived raised IndexError.
            self._raw_buffer: deque = deque(maxlen=self.T)
            self.feature_buffer: deque = deque(maxlen=self.T)
            for i in range(self.T):
                self.feature_buffer.append(self.features[i])
                self._raw_buffer.append(self._raw_features[i])
        self.cfg = cfg
        self.engine = ExecutionEngine(cfg)
        self._breaker_config = (
            cfg.breaker
            if live_mode
            else cfg.breaker.model_copy(update={"state_file": None})
        )
        self.breaker = CircuitBreaker(self._breaker_config)
        self.initial_equity = float(initial_equity)
        # Lot-level overlay of the net position, feeding the graded stop-out
        # cascade.  Purely an accounting twin: execution stays with the engine.
        self._lots = LotLedger()
        self._stop_out_cascade = bool(getattr(cfg.leverage, "stop_out_cascade", True))

        feature_window = features_cfg.rolling_window
        self.warmup_steps = max(200, feature_window)
        if self.T <= self.warmup_steps:
            if not self.live_mode:
                self.warmup_steps = 0

        # An offline step advances t before returning the next observation.
        # There must therefore be a second available bar after warmup.
        # Live mode uses rolling buffers and does not advance t in that way.
        if self.T < 1:
            raise ValueError("Trading environment needs at least one market bar")
        available_steps = self.T - self.warmup_steps - 1
        if not self.live_mode and available_steps < 1:
            raise ValueError(
                "Offline environment needs at least two market bars after warmup"
            )
        self.max_steps = 1000 if max_steps is None else max_steps

        self.reward_scaling = cfg.rewards.scaling
        self.death_penalty = cfg.rewards.death_penalty
        self.reward_clip_min = cfg.rewards.clip_min
        self.reward_clip_max = cfg.rewards.clip_max

        # PHASE 1 UPDATE: Asymmetric Leverage Params
        self._long_max_leverage = float(cfg.leverage.max_leverage)
        self._short_max_leverage = float(cfg.leverage.max_short_leverage)
        self._warn_ratio = float(cfg.leverage.warning_margin_ratio)
        self._liq_ratio = float(cfg.leverage.liquidation_margin_ratio)
        self._stress_mult = float(cfg.slippage.stress_multiplier)

        financing_cfg = cfg.leverage.financing
        self._financing_daily_rate = Decimal(str(financing_cfg.daily_pct)) / Decimal(
            100
        )
        if not self._financing_daily_rate.is_finite() or self._financing_daily_rate < 0:
            raise ValueError("daily financing rate must be finite and nonnegative")
        # Retained as an inspectable nominal-bar value. Actual settlements use
        # elapsed market seconds, including gaps, rather than periods_per_day.
        self._financing_per_bar = float(
            (Decimal(1) + self._financing_daily_rate)
            ** (Decimal(str(self.bar_seconds)) / Decimal(86400))
            - Decimal(1)
        )

        obs_dim = self.n_features + 3
        obs_high = np.full(obs_dim, np.inf, dtype=np.float32)
        self.observation_space = gym.spaces.Box(
            low=-obs_high, high=obs_high, dtype=np.float32
        )

        # PHASE 1 UPDATE: Asymmetric Action Space
        self.action_space = gym.spaces.Box(
            low=-self._short_max_leverage,
            high=self._long_max_leverage,
            shape=(1,),
            dtype=np.float32,
        )

        self.done = False
        self.truncated = False
        self._state = ExecutionState(
            position=Decimal("0.0"),
            equity=Decimal(str(self.initial_equity)),
            borrowed_base=Decimal("0.0"),
            borrowed_quote=Decimal("0.0"),
            average_entry_price=Decimal("0.0"),
        )
        self.prev_mid_price = self.price[-1] if self.live_mode else self.price[0]
        self.equity_history = []
        self.price_history = []
        self.position_history = []

    def reset(
        self, *, seed: Optional[int] = None, options: Optional[Dict[str, Any]] = None
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        super().reset(seed=seed)
        if self.live_mode and self._live_observed_at is not None:
            raise RuntimeError(
                "an observed live account cannot be reset as a training episode"
            )
        if not self.live_mode:
            # Training episodes are independent; never read/write a durable
            # account halt file or inherit the previous episode's loss history.
            self.breaker = CircuitBreaker(self._breaker_config)
        if self.live_mode:
            self.t = len(self.feature_buffer) - 1
        else:
            high = self.T - self.max_steps - 1
            low = self.warmup_steps
            self.t = low if low >= high else int(self.np_random.integers(low, high + 1))

        self.start_t = self.t
        current_price = self.price[-1] if self.live_mode else self.price[self.t]
        self.initial_price = (
            self._account_observation_anchor[1]
            if self.live_mode and self._account_observation_anchor is not None
            else current_price
        )
        self.done = False
        self.truncated = False
        self.financing_accrued = 0.0
        self._lots = LotLedger()
        self._state = ExecutionState(
            position=Decimal("0.0"),
            equity=Decimal(str(self.initial_equity)),
            borrowed_base=Decimal("0.0"),
            borrowed_quote=Decimal("0.0"),
            average_entry_price=Decimal("0.0"),
        )
        self.prev_mid_price = current_price
        self.equity_history: list[float] = [self.initial_equity]
        self.price_history: list[float] = [self.prev_mid_price]
        self.position_history: list[float] = [0.0]
        return self._obs(), {}

    def _build_normalization_arrays(
        self, training_stats: Optional[Dict[str, Any]], columns: list[str]
    ) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if not training_stats:
            return None, None

        center_map = (
            training_stats.get("center", {}) if isinstance(training_stats, dict) else {}
        )
        scale_map = (
            training_stats.get("scale", {}) if isinstance(training_stats, dict) else {}
        )
        centers = np.array(
            [center_map.get(col, 0.0) for col in columns], dtype=np.float32
        )
        scales = np.array(
            [max(scale_map.get(col, 1.0), 1e-6) for col in columns], dtype=np.float32
        )
        return centers, scales

    def _normalize_features_array(self, arr: np.ndarray) -> np.ndarray:
        if self._centers is None or self._scales is None:
            return np.asarray(arr, dtype=np.float32)
        return (np.asarray(arr, dtype=np.float32) - self._centers) / self._scales

    def _get_current_features(self) -> np.ndarray:
        if self.live_mode:
            return self.feature_buffer[-1]
        return self.features[self.t]

    def _get_current_raw_features(self) -> np.ndarray:
        if self.live_mode:
            return self._raw_buffer[-1]
        return self._raw_features[self.t]

    def update_features(self, feature_row: np.ndarray) -> None:
        if not self.live_mode:
            raise RuntimeError("live feature updates require live_mode")
        candidate = np.asarray(feature_row, dtype=np.float32)
        if candidate.shape != (self.n_features,):
            raise ValueError(
                f"live feature row shape {candidate.shape} does not match {(self.n_features,)}"
            )
        if not np.isfinite(candidate).all():
            raise ValueError("live feature row must be finite")
        normalized = self._normalize_features_array(candidate)
        if not np.isfinite(normalized).all():
            raise ValueError("normalised live feature row must be finite")
        self.feature_buffer.append(normalized)
        self._raw_buffer.append(candidate.copy())

    def update_latest_candle(self, price: float) -> None:
        if not np.isfinite(price) or price <= 0:
            raise ValueError("live candle price must be finite and positive")
        idx = -1 if self.live_mode else self.t
        self.price[idx] = self.price_high[idx] = self.price_low[idx] = price

    def step(
        self, action: Union[np.ndarray, float, int]
    ) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """Act on row t, then return the account settled at row t+1.

        A row's OHLC envelope is the completed bar supplying that row's
        features. Only the *next* bar can liquidate inventory acquired by this
        action. This is a bar-level simulation: OHLC cannot resolve intrabar
        event order or the feature recipe's publication-delay overlap.
        """
        if self.done:
            return (
                self._obs(),
                0.0,
                not self.truncated,
                self.truncated,
                {"reason": "episode_done"},
            )

        target_leverage = self._to_float(action)
        if not np.isfinite(target_leverage):
            raise ValueError("Trading action must be finite")
        idx = -1 if self.live_mode else self.t
        current_mid = self.price[idx]
        checked_indices = (idx,) if self.live_mode else (idx, idx + 1)
        if not all(
            np.isfinite(float(value)) and float(value) > 0
            for row in checked_indices
            for value in (self.price[row], self.price_high[row], self.price_low[row])
        ):
            # Reject a corrupt bar *before* mark-to-market. Advancing after a
            # NaN/inf price would poison both equity and the reference mark.
            raise ValueError("Market bar prices must be finite and positive")
        info = StepInfo()
        info.positions[self.cfg.symbol] = float(self._state.position)
        equity_start = float(self._state.equity)

        self._apply_mark_to_market(current_mid)

        skip_trading = self._should_skip_bar()

        context: dict[str, Any] = {
            "nav": self.equity,
            "initial_nav": self.initial_equity,
        }
        # The margin_floor rule defaults a *missing* margin_ratio to infinity
        # (i.e. healthy), so omitting it silently disarmed the rule on the only
        # production call path. The env already computes this value, so supply
        # it -- but only when finite: a flat position has no borrowing and
        # _margin_ratio returns +inf, which the breaker's context validation
        # rejects. Flat means "no margin risk", and absent is already treated
        # as healthy by both the validator and the rule.
        margin_ratio = self._margin_ratio(current_mid)
        if np.isfinite(margin_ratio):
            context["margin_ratio"] = margin_ratio
        if self.breaker.check(context, current_time=self.current_time):
            self.done = True
            info.reason = self.breaker.trip_reason

        if self.done:
            info.equity = float(self.equity)
            info.margin_level = self._margin_level(current_mid)
            return (
                self._obs(),
                self._reward(equity_start),
                not self.truncated,
                self.truncated,
                info.to_dict(),
            )

        if skip_trading:
            # An unsafe spread or invalid feature forbids new trades, not
            # valuation, margin checks, breaker enforcement or financing.
            return self._settle_interval(info, equity_start, current_mid, skipped=True)

        target_position = (target_leverage * float(self.equity)) / max(
            current_mid, 1e-12
        )

        # PHASE 1 UPDATE: Asymmetric Leverage Clipping
        clipped_target = self._clip_to_leverage(target_position)

        # PRECISION FIX: Use str() conversion to avoid float formatting artifacts
        # This ensures we preserve the exact float value without intermediate string formatting
        clipped_target_dec = Decimal(str(clipped_target))
        rebalance = admit_rebalance(
            current_units=self.position,
            target_units=clipped_target_dec,
            equity=self.equity,
            mid=Decimal(str(current_mid)),
            deadband_fraction=Decimal(str(self.cfg.costs.dead_zone_equity_frac)),
        )
        clipped_target_dec = rebalance.target_units

        current_features = self._get_current_raw_features()
        # ROBUSTNESS FIX: Use feature index dictionary exclusively, fail loudly if missing
        # No hardcoded indices or silent fallbacks - this ensures contract integrity
        try:
            spread_idx = self.feature_index["spread_ratio"]
            vol_idx = self.feature_index["volatility"]
            current_spread_bps = float(current_features[spread_idx]) * 10000
            volatility_hint = float(current_features[vol_idx])
        except KeyError as e:
            raise ValueError(
                f"Required feature missing from feature_index: {e}. "
                f"Available features: {list(self.feature_index.keys())}"
            )
        except IndexError as e:
            raise ValueError(
                f"Feature index out of bounds: {e}. "
                f"Features shape: {current_features.shape}, index mapping: {self.feature_index}"
            )

        market = MarketSnapshot(
            mid_price=current_mid,
            spread_bps=current_spread_bps,
            volatility_hint=volatility_hint,
            buy_cap_quote=np.inf,
            sell_cap_quote=np.inf,
        )
        # Pass Decimal to engine (Python runtime allows this despite float type hint)
        result = self.engine.apply_trade(self._state, clipped_target_dec, market)

        info.crossing_cost = float(result.crossing_cost)
        info.slippage_cost = float(result.slippage_cost)
        info.fee_cost = float(result.fee_cost)
        info.liquidity_capped = result.liquidity_capped

        # CRITICAL FIX: ExecutionEngine already calculates average entry price
        # in result.state, so we don't need to recalculate it here.
        # Removed redundant call to self._update_average_entry_price(result)
        self._state = result.state
        # Mirror the executed fill into the lot ledger for stop-out planning.
        if result.delta_units != Decimal("0"):
            self._lots.apply_fill(result.delta_units, result.executed_price)
        return self._settle_interval(info, equity_start, current_mid)

    def _settle_interval(
        self,
        info: StepInfo,
        equity_start: float,
        decision_mid: float,
        *,
        skipped: bool = False,
    ):
        """Value held inventory before exposing the next observation or reward."""
        decision_time = self.current_time
        decision_index = int(self.t)
        self.prev_mid_price = decision_mid
        if not self.live_mode:
            self.t += 1
            ending_mid = float(self.price[self.t])
            elapsed_seconds = self.current_time - decision_time
            self._apply_mark_to_market(ending_mid)
            self._resync_borrows(ending_mid)
        else:
            ending_mid = decision_mid
            elapsed_seconds = self.bar_seconds
        self.prev_mid_price = ending_mid
        self._accrue_financing(info, ending_mid, elapsed_seconds=elapsed_seconds)
        self._check_intra_bar_liquidation(info)
        if not self.done:
            self._check_eod_liquidation(info, ending_mid)
        if not self.done:
            context = {"nav": float(self.equity), "initial_nav": self.initial_equity}
            margin_ratio = self._margin_ratio(ending_mid)
            if np.isfinite(margin_ratio):
                context["margin_ratio"] = margin_ratio
            if self.breaker.check(context, current_time=self.current_time):
                self.done = True
                info.reason = self.breaker.trip_reason
        # A risk termination on the final interval must remain terminated;
        # reaching the horizon must not relabel it as ordinary truncation.
        if (
            not self.done
            and not self.live_mode
            and (self.t >= self.start_t + self.max_steps or self.t >= self.T - 1)
        ):
            self.done = self.truncated = True
        info.equity = float(self.equity)
        info.positions[self.cfg.symbol] = float(self.position)
        info.margin_level = self._margin_level(ending_mid)
        self.equity_history.append(float(self.equity))
        self.price_history.append(ending_mid)
        self.position_history.append(float(self.position))
        terminated, truncated = self._termination_flags()
        metadata = info.to_dict() | {
            "decision_index": decision_index,
            "valuation_index": int(self.t),
            "decision_at": decision_time,
            "valued_at": self.current_time,
            "elapsed_seconds": elapsed_seconds,
        }
        if skipped:
            metadata["skipped"] = True
        return self._obs(), self._reward(equity_start), terminated, truncated, metadata

    @property
    def current_time(self) -> float:
        """Timestamp of this decision; never derive physical time from a live index."""
        if self.live_mode:
            if self._live_observed_at is None:
                raise RuntimeError("live account observation timestamp is required")
            return self._live_observed_at
        return float(self.timestamps[self.t])

    def update_observation_clock(self, observed_at: float) -> None:
        if not self.live_mode:
            raise RuntimeError("external observation clock requires live_mode")
        candidate = float(observed_at)
        if not np.isfinite(candidate):
            raise ValueError("observation time must be finite")
        if self._live_observed_at is not None and candidate < self._live_observed_at:
            raise ValueError("observation time must not move backwards")
        self._live_observed_at = candidate

    def bind_account_observation_anchor(
        self, *, initial_equity: float, initial_price: float
    ) -> None:
        """Admit a durable account reference before the first live observation.

        Equity must match the already admitted model/configuration contract.
        Reset may initialize the observation view but cannot choose a different
        persisted price origin when the account resumes in another process.
        """
        if not self.live_mode or self._live_observed_at is not None:
            raise RuntimeError(
                "account observation anchor must be bound before live observation"
            )
        values = (float(initial_equity), float(initial_price))
        if not all(np.isfinite(v) and v > 0 for v in values):
            raise ValueError("account observation anchors must be finite and positive")
        if values[0] != self.initial_equity:
            raise ValueError(
                "account reference equity differs from admitted model/configuration"
            )
        if (
            self._account_observation_anchor is not None
            and self._account_observation_anchor != values
        ):
            raise ValueError("account observation anchor is already bound")
        self._account_observation_anchor = values

    def observe_account_state(
        self,
        *,
        equity: Decimal,
        position: Decimal,
        mid: float,
        observed_at: float,
        average_entry_price: Decimal = Decimal("0"),
        borrowed_base: Decimal = Decimal("0"),
        borrowed_quote: Decimal = Decimal("0"),
    ) -> bool:
        """Observe an authoritative account without simulating another trade.

        A live observation is already marked by its financial owner. Updating
        this view must not book P&L, fees, financing, liquidation or a zero target.
        The gross-exposure floor below is a conservative local risk estimate;
        it does not claim to be an exchange-reported margin ratio.
        """
        if not self.live_mode:
            raise RuntimeError("external account observations require live_mode")
        values = (equity, position, average_entry_price, borrowed_base, borrowed_quote)
        if not all(isinstance(v, Decimal) and v.is_finite() for v in values):
            raise ValueError("account observation values must be finite Decimal values")
        if borrowed_base < 0 or borrowed_quote < 0 or average_entry_price < 0:
            raise ValueError(
                "account observation contains negative borrow/entry values"
            )
        self.update_observation_clock(observed_at)
        self.update_latest_candle(mid)
        self._state = ExecutionState(
            position, equity, borrowed_base, borrowed_quote, average_entry_price
        )
        self.prev_mid_price = mid
        if not self.equity_history or self.equity_history[-1] != float(equity):
            self.equity_history.append(float(equity))
            self.price_history.append(mid)
            self.position_history.append(float(position))
        context = {"nav": float(equity), "initial_nav": self.initial_equity}
        gross = abs(float(position)) * mid
        if gross > 0:
            context["margin_ratio"] = float(equity) / gross
        self.done = bool(self.breaker.check(context, current_time=self.current_time))
        return self.done

    @property
    def equity(self) -> Decimal:
        return self._state.equity

    @property
    def position(self) -> Decimal:
        return self._state.position

    def render(self):
        """Render the environment to the console."""
        info = self.get_info()
        print("-" * 30)
        print(f"Step: {self.t}")
        print(f"Price: {self.price[-1] if self.live_mode else self.price[self.t]:.2f}")
        print(f"Equity: {info['equity']:.2f}")
        print(f"Position: {info['position']:.4f}")
        print(f"PnL: {info['pnl']:.2f}")
        print("-" * 30)

    def close(self):
        pass

    def _obs(self) -> np.ndarray:
        features = self._get_current_features()
        current_price = self.price[-1] if self.live_mode else self.price[self.t]
        safe_equity = max(float(self.equity), 1e-9)
        state_vars = np.array(
            [
                np.log(safe_equity / self.initial_equity),
                np.log(current_price / self.initial_price),
                float(self.position) / (self.initial_equity / self.initial_price),
            ],
            dtype=np.float32,
        )
        return np.concatenate([features, state_vars])

    @staticmethod
    def _to_float(action):
        if isinstance(action, np.ndarray):
            return float(action.item()) if action.size > 0 else 0.0
        if isinstance(action, list):
            return float(action[0]) if action else 0.0
        return float(action)

    def _apply_mark_to_market(self, current_mid: float) -> None:
        price_diff = Decimal(str(current_mid)) - Decimal(str(self.prev_mid_price))
        self._state.equity += self._state.position * price_diff

    def clip_target_position(self, target_position: float) -> float:
        """
        Public wrapper for _clip_to_leverage to be used by external executors (e.g. LiveExecutionAdapter).
        """
        return self._clip_to_leverage(target_position)

    def _clip_to_leverage(self, target_position: float) -> float:
        """
        Enforce ASYMMETRIC leverage limits per Abantether rules:
        - Long (Credit Trading): max 20x
        - Short (BTC/ETH only): max 5x
        """
        equity_flt = float(self._state.equity)
        idx = -1 if self.live_mode else self.t
        current_price = self.price[idx]

        if target_position >= 0:
            limit = self._long_max_leverage  # 20.0 for longs
        else:
            limit = self._short_max_leverage  # 5.0 for shorts

        max_exposure = limit * max(equity_flt, 0.0)
        max_units = max_exposure / max(current_price, 1e-12)

        return float(np.clip(target_position, -max_units, max_units))

    def _accrue_financing(
        self,
        info: StepInfo,
        current_mid: float,
        *,
        elapsed_seconds: Optional[float] = None,
    ) -> None:
        elapsed = (
            self.bar_seconds if elapsed_seconds is None else float(elapsed_seconds)
        )
        if not np.isfinite(elapsed) or elapsed < 0:
            raise ValueError("financing elapsed seconds must be finite and nonnegative")
        borrowed_val = abs(self._state.borrowed_quote) + abs(
            self._state.borrowed_base
        ) * Decimal(str(current_mid))
        rate = (Decimal(1) + self._financing_daily_rate) ** (
            Decimal(str(elapsed)) / Decimal(86400)
        ) - Decimal(1)
        cost = rate * borrowed_val
        self._state.equity -= cost
        # Funding debits cash; a leveraged long's capitalized quote liability
        # therefore increases even when no new trade occurs on this bar.
        self._resync_borrows(current_mid)
        self.financing_accrued += float(cost)
        info.financing_paid = float(cost)

    def _margin_ratio(self, price: float) -> float:
        borrowed_val = (
            float(abs(self._state.borrowed_quote))
            + float(abs(self._state.borrowed_base)) * price
        )
        if borrowed_val <= 1e-12:
            return float("inf")
        equity_at_price = float(self.equity) + float(self.position) * (
            price - (self.price[-1] if self.live_mode else self.price[self.t])
        )
        return equity_at_price / borrowed_val

    def _margin_level(self, current_mid: float) -> Optional[float]:
        """Equity over gross market exposure at ``current_mid``.

        Broker-faithful risk metric (cf. MetaTrader-style simulators): it is
        the reciprocal of gross leverage and degrades continuously as exposure
        grows, so it is observable signal rather than a gate.
        """
        gross_exposure = abs(float(self._state.position)) * max(current_mid, 1e-12)
        if gross_exposure <= 1e-12:
            return None
        equity = float(self._state.equity)
        if not np.isfinite(equity):
            return None
        return equity / gross_exposure

    def _check_intra_bar_liquidation(self, info: StepInfo) -> None:
        current_mid = self.price[-1] if self.live_mode else self.price[self.t]
        if self.position > 0:
            if self._margin_ratio(self.price_low[self.t]) <= self._liq_ratio:
                self._handle_margin_shortfall(
                    info, float(self.price_low[self.t]), reference_price=current_mid
                )
        elif self.position < 0:
            if self._margin_ratio(self.price_high[self.t]) <= self._liq_ratio:
                self._handle_margin_shortfall(
                    info, float(self.price_high[self.t]), reference_price=current_mid
                )

    def _check_eod_liquidation(self, info: StepInfo, current_mid: float) -> None:
        if self._margin_ratio(current_mid) <= self._liq_ratio:
            self._handle_margin_shortfall(
                info, current_mid, reference_price=current_mid
            )

    def _handle_margin_shortfall(
        self, info: StepInfo, exec_price: float, *, reference_price: float
    ) -> None:
        """Restore margin via the graded stop-out cascade when possible.

        Falls back to the terminal full liquidation when the lot ledger is
        empty (externally constructed state), the cascade is disabled in
        config, or no closure plan can restore the ratio.
        """
        if self.done:
            return
        open_lots = self._lots.open_lots()
        if self._stop_out_cascade and open_lots:
            plan = plan_stop_out(
                lots=open_lots,
                exec_price=Decimal(str(exec_price)),
                reference_price=Decimal(str(reference_price)),
                equity=self._state.equity,
                target_ratio=Decimal(str(self._liq_ratio)),
            )
            for closure in plan.closures:
                # Bookkeeping on the lot twin (realized vs entry feeds
                # attribution surfaces only).
                self._lots.close(
                    lot_id=closure.lot_id,
                    quantity=closure.quantity,
                    exit_price=closure.exit_price,
                )
                # Equity realizes the mark-to-exec slice; entry-to-mark was
                # already absorbed by earlier mark-to-market steps.
                self._state.equity += closure.equity_delta_vs_mark
                info.stop_out_events.append(
                    {
                        "lot_id": closure.lot_id,
                        "quantity": float(closure.quantity),
                        "equity_delta": float(closure.equity_delta_vs_mark),
                        "exit_price": float(closure.exit_price),
                    }
                )
            # Resync net position and borrows with the reduced book.
            new_position = self._lots.net_position()
            self._state.position = new_position
            # Preserve the engine's weighted-average-cost basis on partial
            # reductions; the FIFO lot overlay has separate attribution.
            # A restoring plan can round its close to the entire book, in
            # which case there is no remaining entry basis.
            if new_position == Decimal("0"):
                self._state.average_entry_price = Decimal("0")
            # Remaining inventory/equity are still marked at reference_price;
            # closure execution price must not revalue its outstanding loan.
            self._resync_borrows(reference_price)
            if plan.margin_restored:
                # Margin restored without ending the episode — possibly flat.
                info.graded_stop_out = True
                info.reason = "stop_out_cascade"
                info.liquidation_price = exec_price
                return

        # Terminal path: nothing left to de-risk or cascade could not restore.
        # Equity was already marked to ``reference_price`` (the current mid)
        # by ``_apply_mark_to_market`` at the top of ``step()``; the terminal
        # booking realises only the remaining reference→exec slice.
        pnl = float(self.position) * (exec_price - reference_price)
        self._state.equity += Decimal(str(pnl))
        self._state.position = Decimal("0.0")
        self._lots = LotLedger()
        self._state.borrowed_base = Decimal("0.0")
        self._state.borrowed_quote = Decimal("0.0")
        self._state.average_entry_price = Decimal("0.0")
        info.stop_out_events.clear()
        info.graded_stop_out = False
        info.liquidation = True
        info.liquidation_price = exec_price
        info.reason = "forced_liquidation"
        self.done = True

    def _resync_borrows(self, mark_price: float) -> None:
        """Recompute base borrowing or marked cash liability after a cash change."""
        price = Decimal(str(mark_price))
        if self._state.position >= 0:
            self._state.borrowed_base = Decimal("0.0")
            gross_exposure = abs(self._state.position) * price
            self._state.borrowed_quote = max(
                Decimal("0"), gross_exposure - self._state.equity
            )
        else:
            self._state.borrowed_base = abs(self._state.position)
            self._state.borrowed_quote = Decimal("0.0")

    def _reward(self, equity_before: float) -> float:
        """
        PHASE 2 UPDATE: Softened Death Penalty
        """
        return net_equity_log_reward(
            equity_before,
            float(self.equity),
            scaling=self.reward_scaling,
            death_penalty=self.death_penalty,
            clip_min=self.reward_clip_min,
            clip_max=self.reward_clip_max,
        )

    @property
    def max_steps(self) -> int:
        return self._max_steps

    @max_steps.setter
    def max_steps(self, value: int) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, np.integer))
            or value <= 0
        ):
            raise ValueError("max_steps must be a positive integer")
        available = self.T - self.warmup_steps - 1
        self._max_steps = int(value) if self.live_mode else min(int(value), available)
        if self._max_steps < 1:
            raise ValueError("not enough rows for a transition and its observation")

    def _termination_flags(self) -> Tuple[bool, bool]:
        if not self.done:
            return False, False
        return not self.truncated, bool(self.truncated)

    def _should_skip_bar(self) -> bool:
        """
        Check if the current bar has invalid data or excessive spread.
        """
        # Raw feature row: the normalised observation row is a signed z-score
        # and must not be interpreted as physical basis points.
        current_features = self._get_current_raw_features()
        try:
            # ROBUSTNESS FIX: Use feature index dictionary exclusively
            spread_idx = self.feature_index["spread_ratio"]
            current_spread_bps = float(current_features[spread_idx]) * 10000
        except (KeyError, IndexError):
            # If spread_ratio feature is missing, skip the bar for safety
            logger.warning("spread_ratio feature missing or invalid, skipping bar")
            return True

        max_valid_spread = self.cfg.costs.max_valid_spread_bps
        if current_spread_bps > max_valid_spread:
            return True

        # Check for NaNs in price or features
        idx = -1 if self.live_mode else self.t
        if np.isnan(self.price[idx]):
            return True

        if np.isnan(current_features).any():
            return True

        return False

    @staticmethod
    def _as_read_only(array: np.ndarray, dtype: Any) -> np.ndarray:
        return np.array(array, dtype=dtype, copy=False)

    def get_info(self) -> Dict[str, Any]:
        current_mid = self.price[-1] if self.live_mode else self.price[self.t]
        return {
            "equity": float(self._state.equity),
            "pnl": float(self._state.equity) - self.initial_equity,
            "position": float(self._state.position),
            "entry_price": float(self._state.average_entry_price),
            "timestamp": time.time(),
            "margin_level": self._margin_level(current_mid),
        }
