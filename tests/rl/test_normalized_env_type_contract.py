"""Regression contract for NormalizedMarginEnv type/attribute faithfulness.

These tests pin two production defects that the previous mock hid:

1. ``ExecutionState`` money fields (equity/borrowed_quote/borrowed_base) are
   ``Decimal``. The wrapper mixes them with float prices, so an un-coerced
   ``Decimal * float`` raised ``TypeError`` on the real env while the mock,
   which used floats, stayed green.
2. The wrapper read microstructure values (spread/volatility/caps) and the
   leverage ceiling via ``getattr`` defaults against attribute names the real
   env does not define (``spread_bps``, ``vol``, ``_max_leverage``, ...). On
   the real env those silently fell back to constants -- padding the
   observation with non-information and clipping every action against 0.
"""

import numpy as np
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from gymnasium import spaces

from trading_gym.rl.normalized_env import NormalizedMarginEnv
from trading_gym.rl.margin_trading_environment import MarginTradingEnv


@dataclass
class DecimalState:
    """Mirrors the real ExecutionState: money fields are Decimal."""

    position: float = 0.0
    equity: Decimal = Decimal("1000.0")
    borrowed_quote: Decimal = Decimal("250.0")
    borrowed_base: Decimal = Decimal("1.5")
    average_entry_price: Decimal = Decimal("100.0")


class FaithfulEnv(MarginTradingEnv):
    """Exposes only the interface the real env actually provides."""

    def __init__(self) -> None:
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(12,), dtype=np.float64
        )
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(1,), dtype=np.float32)
        self._state = DecimalState()
        self.initial_equity = 1000.0
        self.price = np.array([100.0] * 100)
        self.T = 100
        self.t = 0
        # Real source for microstructure features: the raw frame + its index.
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
        # Real per-side leverage limits; there is no single _max_leverage.
        self._long_max_leverage = 20.0
        self._short_max_leverage = 5.0

    @property
    def unwrapped(self) -> "FaithfulEnv":
        return self

    def reset(self, seed: int | None = None, options: Any = None):
        return np.zeros(12, dtype=np.float64), {}

    def step(self, action: Any):
        return np.zeros(12, dtype=np.float64), 1.0, False, False, {}

    def render(self) -> None:
        return None


def _wrap(**kwargs: Any) -> NormalizedMarginEnv:
    # Raw (un-normalised) observations so feature values are asserted directly.
    kwargs.setdefault("enable_normalization", False)
    kwargs.setdefault("enable_action_feedback", True)
    return NormalizedMarginEnv(FaithfulEnv(), **kwargs)


def test_decimal_money_fields_survive_observation_and_step():
    """Decimal * float must not raise; pre-fix this raised TypeError."""
    env = _wrap()
    obs, _ = env.reset()
    assert np.isfinite(obs).all(), "observation contained non-finite values"

    # equity (idx 5) is a Decimal on the state; it must surface as a float.
    assert obs[5] == 1000.0
    # margin_ratio (idx 6): borrowed = 250 + 1.5*100 = 400 -> 1000/400 = 2.5.
    assert obs[6] == np.float64(2.5)

    # step() hits the second coercion site (clip ceiling reads Decimal equity).
    obs, reward, terminated, truncated, info = env.step(np.array([0.5]))
    assert np.isfinite(obs).all()
    assert np.isfinite(reward)


def test_microstructure_observed_from_raw_features_not_phantom_attributes():
    """Phantom attribute names must not be read; _raw_features is the source."""
    env = _wrap()

    # Attributes the real env does NOT define. If the wrapper reads them via
    # getattr defaults, these misleading values would leak into the obs.
    base = env.env.unwrapped
    base.spread_bps = 999.0
    base.vol = 999.0
    base.buy_cap = 0.0
    base.sell_cap = 0.0
    base.missing = 999.0

    obs, _ = env.reset()
    # spread_ratio 0.001 -> 10.0 bps; volatility 0.02.
    assert np.isclose(obs[1], 10.0, rtol=1e-5)
    assert np.isclose(obs[2], 0.02, rtol=1e-5)
    # buy/sell capacity present on both sides -> liquidity available.
    assert obs[10] == np.float64(1.0)
    assert obs[11] == np.float64(0.0)


def test_action_clip_uses_real_per_side_leverage():
    """The clip ceiling must use _long_max_leverage, not a phantom _max_leverage."""
    env = _wrap()
    base = env.env.unwrapped
    # A phantom single-limit that, if read, would make the ceiling enormous.
    base._max_leverage = 1e9

    env.reset()
    env.step(np.array([500.0]))

    # max_units = 20 * 1000 / 100 = 200 -> an action of 500 is clipped once.
    # Had the wrapper preferred _max_leverage, no clip would have occurred.
    assert env.action_clips == 1
