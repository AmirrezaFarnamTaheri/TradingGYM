"""Z7 review regressions: the RL env layer's four fixed defects.

Each test pins one defect the Z7 review found, in the form that fails the
moment the fix is reverted:

- ``test_enhanced_obs_features_track_the_current_bar`` (F1): the enhanced
  observation's spread/volatility/liquidity/quality slots are per-bar
  scalars. The buggy implementation wrapped each in a one-element array
  and re-indexed it with the bar number, so every bar after the first hit
  the out-of-range default and the observation padded with constant zeros.
- ``test_short_action_is_bounded_by_the_short_leverage_cap`` (F2): the
  action clip must choose its ceiling by the requested sign, mirroring
  the base env's own ``_clip_to_leverage``. Applying the long cap to both
  sides left a short target between the two caps silently unclipped.
- ``test_live_mode_obs_reads_the_streamed_bar`` (F3): in live mode the
  current raw row is the freshly streamed bar in ``_raw_buffer``, not the
  historical ``_raw_features`` frame indexed by ``t``.
- ``test_missing_required_features_fail_at_construction`` (F8): an env
  whose feature frame lacks ``spread_ratio``/``volatility`` must refuse to
  construct, not defer the failure to the first ``step()``.
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from gymnasium import spaces

from trading_gym.common.config import (
    AttentionConfig,
    BreakerConfig,
    CapacityConfig,
    CostConfig,
    EnvConfig,
    FeatureConfig,
    FinancingConfig,
    LeverageConfig,
    LiquidityConfig,
    RewardConfig,
    ShortsConfig,
    SlippageConfig,
    StandardizationConfig,
    TimeframeConfig,
    VenueDecimals,
)
from trading_gym.rl.margin_trading_environment import MarginTradingEnv
from trading_gym.rl.normalized_env import NormalizedMarginEnv


class _ProbeEnv(MarginTradingEnv):
    """A base env that exposes exactly the attributes the wrapper reads.

    Holding ``t`` fixed lets one observation assert against a known row,
    which is what isolates the F1 indexing bug from everything else the
    base env does on ``step()``.
    """

    def __init__(self, t: int = 0) -> None:
        self.observation_space = spaces.Box(-np.inf, np.inf, shape=(12,), dtype=np.float64)
        self.action_space = spaces.Box(-1.0, 1.0, shape=(1,), dtype=np.float32)
        self._state = type("S", (), {})()
        self._state.position = 0.0
        self._state.equity = Decimal("1000.0")
        self._state.borrowed_quote = Decimal("250.0")
        self._state.borrowed_base = Decimal("1.5")
        self._state.average_entry_price = Decimal("100.0")
        self.initial_equity = 1000.0
        self.price = np.array([100.0] * 100)
        self.T = 100
        self.t = t
        self.feature_index = {
            "spread_ratio": 0,
            "volatility": 1,
            "buy_capacity": 2,
            "sell_capacity": 3,
            "missing_rate": 4,
        }
        self._raw_features = np.array(
            [[0.001, 0.02, 1e6, 1e6, 0.0]] * 100, dtype=np.float32
        )
        self._long_max_leverage = 20.0
        self._short_max_leverage = 5.0

    @property
    def unwrapped(self) -> "_ProbeEnv":
        return self

    def reset(self, seed=None, options=None):  # type: ignore[no-untyped-def]
        return np.zeros(12), {}

    def step(self, action):  # type: ignore[no-untyped-def]
        return np.zeros(12), 1.0, False, False, {}


def test_enhanced_obs_features_track_the_current_bar() -> None:
    """F1: spread/volatility/liquidity slots must not collapse to zero."""

    for t in (0, 5, 40):
        env = NormalizedMarginEnv(_ProbeEnv(t), enable_normalization=False)
        obs, _ = env.reset()
        # spread_ratio 0.001 -> 10 bps; volatility 0.02; both capacities
        # present -> liquidity 1.0; missing_rate 0.0.
        assert obs[1] == pytest.approx(10.0), f"t={t}: spread collapsed"
        assert obs[2] == pytest.approx(0.02), f"t={t}: volatility collapsed"
        assert obs[10] == pytest.approx(1.0), f"t={t}: liquidity collapsed"
        assert obs[11] == pytest.approx(0.0), f"t={t}: quality drifted"


def test_short_action_is_bounded_by_the_short_leverage_cap() -> None:
    """F2: a short target between the two caps must still be clipped."""

    env = NormalizedMarginEnv(_ProbeEnv(0), enable_normalization=False)
    env.reset()
    # equity 1000 at price 100: the 20x long cap allows 200 units, the 5x
    # short cap allows 50. An action of -100 sits between them: correct
    # sign-aware clipping flags it, the long-cap-for-both-sides bug did not.
    env.step(np.array([-100.0]))
    assert env.action_clips == 1, "short target between the caps was not clipped"


def test_live_mode_obs_reads_the_streamed_bar() -> None:
    """F3: live mode observes the streamed bar, not the historical frame."""

    class _LiveProbe(MarginTradingEnv):
        def __init__(self) -> None:
            n = 60
            cols = ["log_ret", "volatility", "rsi", "spread_ratio"]
            feat = np.zeros((n, len(cols)), dtype=np.float32)
            feat[:, 1] = 0.02
            feat[:, 3] = 0.001
            self.live_mode = True
            self.feature_index = {c: i for i, c in enumerate(cols)}
            self._raw_features = feat.copy()
            self.features = feat.copy()
            self.price = np.array([100.0] * n)
            self.price_high = self.price_low = self.price
            self.T = n
            self.n_features = len(cols)
            self._state = type("S", (), {})()
            self._state.position = Decimal("0")
            self._state.equity = Decimal("1000.0")
            self._state.borrowed_quote = Decimal("0")
            self._state.borrowed_base = Decimal("0")
            self._state.average_entry_price = Decimal("0")
            self.initial_equity = 1000.0
            self.t = n - 1
            self._long_max_leverage = 20.0
            self._short_max_leverage = 5.0
            self._raw_buffer = deque(maxlen=n)
            for i in range(n):
                self._raw_buffer.append(feat[i])
            # a freshly streamed bar the historical frame does not hold
            self._raw_buffer.append(
                np.array([0.0, 0.09, 0.0, 0.005], dtype=np.float32)
            )

        @property
        def unwrapped(self) -> "_LiveProbe":
            return self

        def reset(self, seed=None, options=None):  # type: ignore[no-untyped-def]
            return np.zeros(12), {}

        def step(self, action):  # type: ignore[no-untyped-def]
            return np.zeros(12), 1.0, False, False, {}

    env = NormalizedMarginEnv(_LiveProbe(), enable_normalization=False)
    obs, _ = env.reset()
    assert obs[1] == pytest.approx(50.0), "spread not from the streamed bar"
    assert obs[2] == pytest.approx(0.09), "volatility not from the streamed bar"


def _env_config() -> EnvConfig:
    return EnvConfig(
        symbol="BTCUSDT",
        quote_ccy="USDT",
        decimals=VenueDecimals(price={"BTCUSDT": 2}, qty={"BTCUSDT": 8}),
        leverage=LeverageConfig(
            min_leverage=1.0,
            max_leverage=20.0,
            max_short_leverage=5.0,
            warning_margin_ratio=0.10,
            liquidation_margin_ratio=0.03,
            financing=FinancingConfig(daily_pct=0.0, periods_per_day=288),
        ),
        shorts=ShortsConfig(
            enabled=True,
            allowed_symbols=["BTCUSDT"],
            borrow_caps={"BTCUSDT": 10.0},
        ),
        costs=CostConfig(
            taker_fee_bps=1.0, dead_zone_equity_frac=0.0001, max_spread_bps=50.0
        ),
        slippage=SlippageConfig(
            volatility_lambda=0.0,
            liquidity_lambda=0.0,
            stress_multiplier=1.0,
            volume_reference_fraction=0.01,
        ),
        liquidity=LiquidityConfig(default_quote_cap=1_000_000.0),
        capacity=CapacityConfig(conservative_default=10_000_000.0),
        breaker=BreakerConfig(
            max_loss_pct=1e9,
            max_loss_velocity_pct_per_hour=1e9,
            max_single_trade_loss_pct=1e9,
            margin_check_enabled=False,
            stall_check_enabled=False,
        ),
        rewards=RewardConfig(
            scaling=1.0, death_penalty=-100.0, clip_min=-10.0, clip_max=10.0
        ),
    )


def test_missing_required_features_fail_at_construction() -> None:
    """F8: absent spread_ratio/volatility must surface at __init__."""

    n = 250
    cols = ["log_ret", "rsi"]
    feat = pd.DataFrame(np.zeros((n, len(cols)), dtype=np.float32), columns=cols)
    prices = np.full(n, 100.0)
    with pytest.raises(ValueError, match="spread_ratio"):
        MarginTradingEnv(
            features=feat,
            prices_mid=prices,
            prices_high=prices,
            prices_low=prices,
            cfg=_env_config(),
            features_cfg=FeatureConfig(
                timeframes=[
                    TimeframeConfig(name="5m", bar_minutes=5, roll_fill_cap=12)
                ],
                standardization=StandardizationConfig(
                    method="robust", fit_window_bars=100
                ),
                attention=AttentionConfig(enabled=False),
                rolling_window=200,
            ),
            training_stats={"mean": 0.0, "std": 1.0},
        )
