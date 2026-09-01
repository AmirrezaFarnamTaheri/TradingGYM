import numpy as np
from unittest.mock import MagicMock
from trading_gym.rl.normalized_env import NormalizedMarginEnv
from trading_gym.rl.margin_trading_environment import MarginTradingEnv
from gymnasium import spaces
from dataclasses import dataclass


@dataclass
class MockState:
    position: float = 0.0
    equity: float = 1000.0
    borrowed_quote: float = 0.0
    borrowed_base: float = 0.0
    average_entry_price: float = 100.0


class MockEnv(MarginTradingEnv):
    def __init__(self):
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(10,), dtype=np.float32
        )
        self.action_space = spaces.Box(low=-1.0, high=1.0, shape=(1,), dtype=np.float32)
        self._state = MockState()
        self.initial_equity = 1000.0
        self.price = np.array([100.0] * 100)
        self.spread_bps = np.array([10.0] * 100)
        self.vol = np.array([0.01] * 100)
        self.buy_cap = np.array([1e6] * 100)
        self.sell_cap = np.array([1e6] * 100)
        self.missing = np.array([0.0] * 100)
        self.T = 100
        self.t = 0
        self._max_leverage = 20.0

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
