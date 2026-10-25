"""
Borrow Cap Integration for RL Environments
===========================================

Wrappers and helpers to enforce broker borrow caps in RL training.

This ensures that the RL agent learns within realistic constraints
and won't produce actions that would be rejected in live trading.
"""

from typing import Dict, Any, Tuple, Optional, Union, cast
import numpy as np
import gymnasium as gym
import logging

# Change import to avoid circular dependency or missing import from __init__
# We import directly from the implementation files
from trading_gym.core.config import BorrowCapConfig
from trading_gym.execution.margin.borrow_caps import clip_short_quantity

logger = logging.getLogger(__name__)


class BorrowCapActionClipper:
    """
    Clips RL actions to respect borrow caps.

    Use this in your environment's step() function to ensure
    actions don't violate broker short selling limits.
    """

    def __init__(self, config: BorrowCapConfig):
        """
        Initialize action clipper.

        Args:
            config: Borrow cap configuration
        """
        self.config = config
        logger.info(
            f"BorrowCapActionClipper initialized with caps: {config.borrow_caps}"
        )

    def clip_action(
        self, symbol: str, action: float, current_position: float = 0.0
    ) -> float:
        """
        Clip action to respect borrow caps.

        Args:
            symbol: Trading symbol (e.g., "BTC")
            action: Raw action from agent (can be negative for shorts)
            current_position: Current position (negative = short)

        Returns:
            Clipped action that respects borrow caps

        Example:
            >>> clipper = BorrowCapActionClipper(config)
            >>> # Agent wants to short 0.8 BTC, but cap is 0.5
            >>> action = -0.8
            >>> clipped = clipper.clip_action("BTC", action, current_position=0.0)
            >>> print(clipped)  # -0.5 (clipped to cap)
        """
        if action >= 0:  # Long positions, no borrow cap
            return action

        # For short positions, check cap
        # Convert to positive magnitude for clipping, then reapply sign
        desired_short_qty = abs(action)
        capped_qty = clip_short_quantity(symbol, desired_short_qty, self.config)
        clipped = -capped_qty

        if clipped != action:
            logger.debug(
                f"Action clipped for {symbol}: {action:.4f} -> {clipped:.4f} "
                f"(cap: {self.config.borrow_caps.get(symbol, 'N/A')})"
            )

        return clipped

    def clip_action_dict(
        self,
        actions: Dict[str, float],
        current_positions: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """
        Clip actions for multiple symbols.

        Args:
            actions: Dict of {symbol: action}
            current_positions: Dict of {symbol: current_position}

        Returns:
            Dict of clipped actions
        """
        if current_positions is None:
            current_positions = {}

        clipped = {}
        for symbol, action in actions.items():
            current_pos = current_positions.get(symbol, 0.0)
            clipped[symbol] = self.clip_action(symbol, action, current_pos)

        return clipped


class BorrowCapEnvWrapper(gym.Wrapper):
    """
    Gym environment wrapper that enforces borrow caps.

    This wrapper automatically clips actions to respect broker limits,
    so the RL agent never sees rejected orders during training.

    Usage:
        >>> from trading_gym.rl import BorrowCapEnvWrapper
        >>> from trading_gym.execution.margin import BorrowCapConfig
        >>>
        >>> # Your base environment
        >>> env = YourTradingEnv()
        >>>
        >>> # Wrap with borrow cap enforcement
        >>> config = BorrowCapConfig(
        ...     borrow_caps={"BTC": 0.5, "ETH": 10.0},
        ...     allowed_symbols=["BTC", "ETH"],
        ...     enabled=True
        ... )
        >>> env = BorrowCapEnvWrapper(env, config)
        >>>
        >>> # Now all actions are automatically clipped
        >>> obs, info = env.reset()
        >>> action = agent.get_action(obs)  # e.g., short 0.8 BTC
        >>> obs, reward, terminated, truncated, info = env.step(action)  # Clipped to 0.5
    """

    def __init__(
        self,
        env: gym.Env,
        config: BorrowCapConfig,
        symbol_mapping: Optional[Dict[int, str]] = None,
        position_key: str = "position",
    ):
        """
        Initialize wrapper.

        Args:
            env: Base gym environment
            config: Borrow cap configuration
            symbol_mapping: Mapping from action index to symbol (for multi-asset envs)
            position_key: Key in info dict containing current position
        """
        super().__init__(env)
        self.config = config
        self.symbol_mapping = symbol_mapping
        self.position_key = position_key
        self.clipper = BorrowCapActionClipper(config)
        # Track last known positions per symbol for action clipping
        self._last_positions: Dict[str, float] = {}

        symbols = list(symbol_mapping.values()) if symbol_mapping else "single-asset"
        logger.info(f"BorrowCapEnvWrapper initialized for symbols: {symbols}")

    def step(
        self, action: Union[np.ndarray, float]
    ) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """
        Step environment with clipped action.

        Args:
            action: Raw action from agent

        Returns:
            (observation, reward, terminated, truncated, info) with clipped action applied
        """
        # Get current position from last info (if available)
        current_positions = getattr(self, "_last_positions", {})

        # Clip action
        # Prepare variable with a broad type to satisfy mypy
        clipped_action: Union[np.ndarray, float]

        if self.symbol_mapping:
            # When using symbol_mapping, actions are expected to be an ndarray
            assert isinstance(action, np.ndarray)
            clipped_arr = np.copy(action)
            for i, symbol in self.symbol_mapping.items():
                if i < len(action):
                    current_position = current_positions.get(symbol, 0.0)
                    clipped_arr[i] = self.clipper.clip_action(
                        symbol, action[i], current_position
                    )
            clipped_action = clipped_arr
        else:
            # Single-asset environment
            if isinstance(action, np.ndarray):
                action_value = float(action[0])
            else:
                action_value = float(action)

            current_position = current_positions.get("default", 0.0)
            clipped_value = self.clipper.clip_action(
                self.config.allowed_symbols[0], action_value, current_position
            )

            if isinstance(action, np.ndarray):
                clipped_action = np.array([clipped_value], dtype=action.dtype)
            else:
                clipped_action = clipped_value

        # Execute step with clipped action
        step_result = self.env.step(clipped_action)

        # STRICT GYMNASIUM ENFORCEMENT
        # Remove the legacy check "if len(step_result) == 4"
        if len(step_result) != 5:
            raise RuntimeError(
                f"Environment returned {len(step_result)} values. "
                "Strict Gymnasium API (5-tuple) is required."
            )

        obs, reward, terminated, truncated, info = step_result

        # Store position for next step
        if self.position_key in info:
            # Info may contain positions per symbol or a single default
            pos_val = info[self.position_key]
            if isinstance(pos_val, dict):
                for k, v in pos_val.items():
                    try:
                        self._last_positions[str(k)] = float(v)
                    except Exception:
                        pass
            else:
                self._last_positions["default"] = float(pos_val)

        # Add clipping info
        info["action_clipped"] = not np.allclose(action, clipped_action)
        if info["action_clipped"]:
            info["original_action"] = action
            info["clipped_action"] = clipped_action

        return obs, reward, terminated, truncated, info

    def reset(self, **kwargs: Any) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Reset environment and clear position tracking."""
        self._last_positions = {}
        result = self.env.reset(**kwargs)
        return cast(Tuple[np.ndarray, Dict[str, Any]], result)


class BorrowCapRewardShaper:
    """
    Optional: Reward shaper that penalizes attempts to violate borrow caps.

    This can help the agent learn to avoid cap violations faster.
    """

    def __init__(self, penalty: float = -1.0, scaling: str = "fixed"):
        """
        Initialize reward shaper.

        Args:
            penalty: Penalty for attempting to violate cap
            scaling: "fixed" or "proportional" (to violation magnitude)
        """
        self.penalty = penalty
        self.scaling = scaling

    def shape_reward(
        self,
        base_reward: float,
        action_clipped: bool,
        original_action: Optional[float] = None,
        clipped_action: Optional[float] = None,
    ) -> float:
        """
        Add penalty if action was clipped.

        Args:
            base_reward: Original reward from environment
            action_clipped: Whether action was clipped
            original_action: Original action value
            clipped_action: Clipped action value

        Returns:
            Shaped reward
        """
        if not action_clipped:
            return base_reward

        if (
            self.scaling == "proportional"
            and original_action is not None
            and clipped_action is not None
        ):
            # Penalty proportional to how much was clipped
            violation_magnitude = abs(original_action - clipped_action)
            penalty = self.penalty * violation_magnitude
        else:
            # Fixed penalty
            penalty = self.penalty

        shaped_reward = base_reward + penalty

        logger.debug(
            f"Reward shaped: {base_reward:.4f} -> {shaped_reward:.4f} "
            f"(penalty: {penalty:.4f})"
        )

        return shaped_reward


# Example integration function
def create_borrow_cap_env(
    base_env: gym.Env,
    config: BorrowCapConfig,
    symbol_mapping: Optional[Dict[int, str]] = None,
    use_reward_shaping: bool = False,
) -> gym.Env:
    """
    Convenience function to create a borrow-cap-enforced environment.

    Args:
        base_env: Your trading environment
        config: Borrow cap configuration
        symbol_mapping: Mapping from action index to symbol (for multi-asset envs)
        use_reward_shaping: Whether to add penalty for cap violations

    Returns:
        Wrapped environment with borrow cap enforcement

    Example:
        >>> env = TradingEnv()
        >>> config = BorrowCapConfig(...)
        >>> env = create_borrow_cap_env(env, config)
        >>> # Now ready for training with enforced caps
    """
    # Wrap environment
    env = BorrowCapEnvWrapper(base_env, config, symbol_mapping=symbol_mapping)

    logger.info("Created borrow-cap environment.")

    return env
