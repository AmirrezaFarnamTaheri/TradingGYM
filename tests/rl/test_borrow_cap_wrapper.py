import pytest
import numpy as np
import gymnasium as gym
from unittest.mock import MagicMock
from trading_gym.rl.borrow_cap_wrapper import (
    BorrowCapActionClipper,
    BorrowCapEnvWrapper,
    create_borrow_cap_env,
    BorrowCapConfig,
)


@pytest.fixture
def borrow_config():
    return BorrowCapConfig(
        enabled=True, allowed_symbols=["BTC"], borrow_caps={"BTC": 0.5}
    )


def test_clipper_init(borrow_config):
    clipper = BorrowCapActionClipper(borrow_config)
    assert clipper.config == borrow_config


def test_clipper_long(borrow_config):
    clipper = BorrowCapActionClipper(borrow_config)
    # Longs pass through
    assert clipper.clip_action("BTC", 1.0) == 1.0
    assert clipper.clip_action("BTC", 100.0) == 100.0


def test_clipper_short_within_cap(borrow_config):
    clipper = BorrowCapActionClipper(borrow_config)
    # Short 0.4 (Cap 0.5) -> -0.4
    assert clipper.clip_action("BTC", -0.4) == -0.4


def test_clipper_short_exceeds_cap(borrow_config):
    clipper = BorrowCapActionClipper(borrow_config)
    # Short 0.8 (Cap 0.5) -> -0.5
    # Logic: clip_short_quantity checks desired_qty vs cap.
    # Returns min(desired, cap).
    # Then re-applies negative sign.
    assert clipper.clip_action("BTC", -0.8) == -0.5


def test_clipper_not_allowed_symbol(borrow_config):
    clipper = BorrowCapActionClipper(borrow_config)
    # ETH not allowed -> Cap is 0.0 (implied if enabled=True but not in list?
    # Actually clip_short_quantity logic handles it:
    # if symbol not in allowed_symbols -> 0.0
    assert clipper.clip_action("ETH", -0.1) == 0.0


class MockGymEnv(gym.Env):
    def __init__(self):
        self.observation_space = gym.spaces.Box(low=-1, high=1, shape=(1,))
        self.action_space = gym.spaces.Box(low=-1, high=1, shape=(1,))

    def step(self, action):
        return np.zeros(1), 0.0, False, False, {}

    def reset(self, **kwargs):
        return np.zeros(1), {}


def test_wrapper_step():
    mock_env = MockGymEnv()
    # We need to spy on step, so we wrap it
    mock_env.step = MagicMock(side_effect=mock_env.step)

    config = BorrowCapConfig(
        enabled=True, allowed_symbols=["BTC"], borrow_caps={"BTC": 0.5}
    )

    env = BorrowCapEnvWrapper(mock_env, config)

    # Step with excessive short
    env.step(np.array([-1.0]))

    # Check if underlying env called with clipped action
    args, _ = mock_env.step.call_args
    clipped_action = args[0]
    assert clipped_action[0] == -0.5


def test_wrapper_info_feedback():
    mock_env = MockGymEnv()
    config = BorrowCapConfig(
        enabled=True, allowed_symbols=["BTC"], borrow_caps={"BTC": 0.5}
    )

    env = BorrowCapEnvWrapper(mock_env, config)
    _, _, _, _, info = env.step(np.array([-1.0]))

    assert info["action_clipped"] is True
    assert info["clipped_action"][0] == -0.5


def test_create_helper():
    mock_env = MockGymEnv()
    config = BorrowCapConfig(enabled=True, allowed_symbols=[], borrow_caps={})
    env = create_borrow_cap_env(mock_env, config)
    assert isinstance(env, BorrowCapEnvWrapper)
