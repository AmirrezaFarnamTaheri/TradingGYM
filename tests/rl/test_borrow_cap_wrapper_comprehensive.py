import pytest
import numpy as np
from unittest.mock import Mock
import gymnasium as gym
from trading_gym.rl.borrow_cap_wrapper import BorrowCapEnvWrapper
from trading_gym.core.config import BorrowCapConfig


class TestBorrowCapEnvWrapper:
    @pytest.fixture
    def base_env(self):
        env = Mock(spec=gym.Env)
        env.unwrapped = env
        env.observation_space = gym.spaces.Box(low=0, high=1, shape=(5,))
        env.action_space = gym.spaces.Box(low=-1, high=1, shape=(1,))
        env.reset.return_value = (np.zeros(5), {})
        env.step.return_value = (np.zeros(5), 0.0, False, False, {})

        # Mock env state attributes for fallback checks in wrapper?
        # Actually wrapper uses info to update positions.

        return env

    @pytest.fixture
    def shorts_config(self):
        return BorrowCapConfig(
            borrow_caps={"BTC": 0.5}, allowed_symbols=["BTC"], enabled=True
        )

    def test_initialization(self, base_env, shorts_config):
        wrapper = BorrowCapEnvWrapper(base_env, config=shorts_config)
        assert wrapper.config.enabled is True
        assert wrapper.config.borrow_caps["BTC"] == 0.5

    def test_step_clipping(self, base_env, shorts_config):
        wrapper = BorrowCapEnvWrapper(base_env, config=shorts_config)

        # Manually set last position (wrapper relies on info dict from previous step)
        wrapper._last_positions = {"default": 0.0}

        # Action -0.6 on BTC (cap 0.5). Should clip to -0.5
        action = np.array([-0.6])

        # Step returns info that wrapper processes
        base_env.step.return_value = (
            np.zeros(5),
            0.0,
            False,
            False,
            {"position": -0.5},
        )

        wrapper.step(action)

        # Verify base_env called with clipped action
        args, _ = base_env.step.call_args
        called_action = args[0]
        assert np.isclose(called_action[0], -0.5)

    def test_step_no_clipping(self, base_env, shorts_config):
        wrapper = BorrowCapEnvWrapper(base_env, config=shorts_config)
        wrapper._last_positions = {"default": 0.0}

        action = np.array([-0.4])
        wrapper.step(action)

        args, _ = base_env.step.call_args
        assert np.isclose(args[0][0], -0.4)

    def test_reset(self, base_env, shorts_config):
        wrapper = BorrowCapEnvWrapper(base_env, config=shorts_config)
        wrapper._last_positions = {"default": -100.0}

        wrapper.reset()
        assert wrapper._last_positions == {}
        base_env.reset.assert_called_once()
