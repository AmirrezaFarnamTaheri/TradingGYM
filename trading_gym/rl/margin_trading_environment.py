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

from trading_gym.common.config import EnvConfig, FeatureConfig
from trading_gym.execution.execution_engine import (
    ExecutionEngine,
    ExecutionState,
    MarketSnapshot,
)
from trading_gym.margin.lot_positions import LotLedger
from trading_gym.margin.stop_out import plan_stop_out
from trading_gym.risk.circuit_breakers import CircuitBreaker

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
        self.breaker = CircuitBreaker(cfg.breaker)
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

        available_steps = self.T - self.warmup_steps - 1
        self.max_steps = min(1000, available_steps)
        if self.max_steps <= 0:
            self.max_steps = 1

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
        # CRITICAL FIX: daily_pct is in percentage form (e.g., 0.05 means 0.05%)
        # Must convert to decimal: 0.05 / 100 = 0.0005
        # The previous code treated 0.05 as 5% (0.05 in decimal form), which is 100x too high!
        # Formula: rate_per_bar = (1 + daily_rate_decimal)^(1/periods_per_day) - 1
        daily_rate_decimal = financing_cfg.daily_pct / 100.0
        self._financing_per_bar = (1.0 + daily_rate_decimal) ** (
            1.0 / financing_cfg.periods_per_day
        ) - 1.0

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
        if self.live_mode:
            self.t = len(self.feature_buffer) - 1
        else:
            high = self.T - self.max_steps - 1
            low = self.warmup_steps
            self.t = low if low >= high else self.np_random.integers(low, high)

        self.start_t = self.t
        current_price = self.price[-1] if self.live_mode else self.price[self.t]
        self.initial_price = current_price
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
        if self.done:
            return (
                self._obs(),
                self._reward(float(self._state.equity)),
                not self.truncated,
                self.truncated,
                {"reason": "episode_done"},
            )

        target_leverage = self._to_float(action)
        current_mid = self.price[-1] if self.live_mode else self.price[self.t]
        info = StepInfo()
        info.positions[self.cfg.symbol] = float(self._state.position)
        equity_start = float(self._state.equity)

        self._apply_mark_to_market(current_mid)

        if self._should_skip_bar():
            self._accrue_financing(info, current_mid)
            self._advance_time(current_mid)
            terminated, truncated = self._termination_flags()
            info.equity = float(self._state.equity)
            info.margin_level = self._margin_level(current_mid)
            return (
                self._obs(),
                0.0,
                terminated,
                truncated,
                info.to_dict() | {"skipped": True},
            )

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
        if self.breaker.check(context, current_time=self.t * 60):
            self.done = True
            info.reason = self.breaker.trip_reason

        self._check_intra_bar_liquidation(info)

        if self.done:
            return (
                self._obs(),
                self._reward(equity_start),
                not self.truncated,
                self.truncated,
                info.to_dict(),
            )

        target_position = (target_leverage * float(self.equity)) / max(
            current_mid, 1e-12
        )

        # PHASE 1 UPDATE: Asymmetric Leverage Clipping
        clipped_target = self._clip_to_leverage(target_position)

        # PRECISION FIX: Use str() conversion to avoid float formatting artifacts
        # This ensures we preserve the exact float value without intermediate string formatting
        clipped_target_dec = Decimal(str(clipped_target))

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
        self._accrue_financing(info, current_mid)
        self._check_eod_liquidation(info, current_mid)

        reward = self._reward(equity_start)
        info.equity = float(self._state.equity)
        info.margin_level = self._margin_level(current_mid)

        self._advance_time(current_mid)
        terminated, truncated = self._termination_flags()

        self.equity_history.append(float(self._state.equity))
        self.price_history.append(current_mid)
        self.position_history.append(float(self._state.position))

        return self._obs(), reward, terminated, truncated, info.to_dict()

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
        price_diff = Decimal(str(current_mid - self.prev_mid_price))
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

    def _accrue_financing(self, info: StepInfo, current_mid: float) -> None:
        borrowed_val = abs(self._state.borrowed_quote) + abs(
            self._state.borrowed_base
        ) * Decimal(str(current_mid))
        rate = Decimal(str(self._financing_per_bar))
        cost = rate * borrowed_val
        self._state.equity -= cost
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
            self._resync_borrows(exec_price)
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
        info.stop_out_events.clear()
        info.graded_stop_out = False
        info.liquidation = True
        info.liquidation_price = exec_price
        info.reason = "forced_liquidation"
        self.done = True

    def _resync_borrows(self, exec_price: float) -> None:
        """Recompute borrow state from the post-cascade position (engine rule)."""
        price = Decimal(str(exec_price))
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
        if float(self.equity) <= 0.0:
            return self.death_penalty
        return np.clip(
            float(np.log(float(self.equity) / equity_before) * self.reward_scaling),
            self.reward_clip_min,
            self.reward_clip_max,
        )

    def _advance_time(self, current_mid: float) -> None:
        self.prev_mid_price = current_mid
        if not self.live_mode:
            self.t += 1
        if self.t >= self.start_t + self.max_steps:
            self.done = self.truncated = True

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
