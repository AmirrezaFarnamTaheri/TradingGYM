import numpy as np
from unittest.mock import MagicMock
from trading_gym.rl.normalized_env import NormalizedMarginEnv
from trading_gym.rl.margin_trading_environment import MarginTradingEnv
from gymnasium import spaces
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class MockState:
    position: float = 0.0
    # Money fields are Decimal on the real ExecutionState; keep the mock
    # faithful so any Decimal/float clash fails here, not in production.
    equity: Decimal = Decimal("1000.0")
    borrowed_quote: Decimal = Decimal("0.0")
    borrowed_base: Decimal = Decimal("1.5")
    average_entry_price: Decimal = Decimal("100.0")


class MockEnv(MarginTradingEnv):
    def __init__(self):
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(10,), dtype=np.float32
        )
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(1,), dtype=np.float32)
        self._state = MockState()
        self.initial_equity = 1000.0
        self.price = np.array([100.0] * 100)
        # Expose the real interface the wrapper reads: feature_index plus the
        # raw (unnormalised) feature frame, not phantom per-bar attributes.
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
        self.T = 100
        self.t = 0
        # The real env exposes per-side limits, not a single _max_leverage.
        self._long_max_leverage = 20.0
        self._short_max_leverage = 5.0

    @property
    def unwrapped(self):
        return self

    def reset(self, seed=None, options=None):
        return np.zeros(10, dtype=np.float32), {}

    def step(self, action):
        return np.zeros(10, dtype=np.float32), 1.0, False, False, {}

    def render(self):
        return None


def test_normalized_env_init():
    env = MockEnv()
    norm_env = NormalizedMarginEnv(env)
    assert isinstance(norm_env, NormalizedMarginEnv)


def test_normalized_env_step_clips_reward():
    env = MockEnv()
    norm_env = NormalizedMarginEnv(env, reward_clip=0.5)
    obs, reward, terminated, truncated, info = norm_env.step(np.array([0.0]))
    # 1.0 -> 0.5
    assert reward == 0.5


def test_normalized_env_step_normalizes_obs():
    env = MockEnv()
    norm_env = NormalizedMarginEnv(env)

    obs, reward, terminated, truncated, info = norm_env.step(np.array([0.0]))
    # Enhanced obs is 12 dims
    assert obs.shape == (12,)


def test_normalized_env_survives_decimal_money_fields():
    """Regression: ExecutionState money fields are Decimal.

    Decimal * float raises TypeError, so the wrapper and the step() action
    clip must coerce to float before mixing them.  A mock with float fields
    hid this for a long time; keep the mock Decimal and pin the behaviour.
    """
    env = MockEnv()
    # Nonzero borrowed_base exercises the Decimal * current_mid path.
    env._state.borrowed_base = Decimal("2.5")
    env._state.borrowed_quote = Decimal("100.0")
    env._state.equity = Decimal("500.0")
    norm_env = NormalizedMarginEnv(env)

    obs, reward, terminated, truncated, info = norm_env.step(np.array([0.5]))
    assert obs.shape == (12,)
    assert np.isfinite(obs).all()

    obs2, info2 = norm_env.reset()
    assert obs2.shape == (12,)
    assert np.isfinite(obs2).all()


def test_normalized_env_reset():
    env = MockEnv()
    norm_env = NormalizedMarginEnv(env)
    obs, info = norm_env.reset()
    assert obs.shape == (12,)  # Enhanced obs default
    assert isinstance(info, dict)


def test_normalized_env_update_latest_candle():
    env = MockEnv()
    env.update_latest_candle = MagicMock()
    norm_env = NormalizedMarginEnv(env)

    candle = {"close": 100.0}
    # NormalizedMarginEnv doesn't implement update_latest_candle directly,
    # it relies on accessing the underlying env.
    # BUT Wrapper delegation via __getattr__ is what happens usually.
    # However, if we access it on norm_env, it should delegate.

    norm_env.update_latest_candle(candle)
    env.update_latest_candle.assert_called_with(candle)


def test_normalization_stats_computation():
    env = MockEnv()
    # Provide stats dict
    stats = {
        "price_mean": 100.0,
        "price_std": 10.0,
        "spread_mean": 5.0,
        "spread_std": 1.0,
        "vol_mean": 0.01,
        "vol_std": 0.005,
    }
    norm_env = NormalizedMarginEnv(env, training_stats=stats)

    assert norm_env.obs_mean[0] == 100.0
    assert norm_env.obs_mean[1] == 5.0
    assert norm_env.obs_mean[2] == 0.01
