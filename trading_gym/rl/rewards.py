"""Pluggable reward functions for RL trading environments.

Absorbed pattern: reward functions receive the full episode history so shaping
does not require environment forks (cf. gym_trading_env).  Every reward is a
pure function of plain arrays, Decimal-free and side-effect free; the default
(:func:`log_return_reward`) reproduces the historical ``MarginTradingEnv``
reward exactly, so it can be swapped in without behaviour change.
"""

from __future__ import annotations

from typing import Callable, Protocol

import numpy as np



class RewardContext(Protocol):
    """Minimal view of an episode step used by reward functions."""

    @property
    def equity_before(self) -> float: ...

    @property
    def equity_after(self) -> float: ...

    @property
    def equity_history(self) -> np.ndarray: ...

    @property
    def position_before(self) -> float: ...

    @property
    def position_after(self) -> float: ...

RewardFunction = Callable[[RewardContext], float]


def log_return_reward(ctx: RewardContext) -> float:
    """Log wealth return for the step — the platform default."""
    before = max(ctx.equity_before, 1e-12)
    if ctx.equity_after <= 0.0:
        # Non-positive wealth is handled by the death penalty upstream; keep
        # the function total by returning the log of a tiny positive number.
        return float(np.log(1e-12 / before))
    return float(np.log(ctx.equity_after / before))


def drawdown_penalised_reward(ctx: RewardContext, *, lambda_dd: float = 5.0) -> float:
    """Log return penalised by the running peak-to-now drawdown fraction.

    ``lambda_dd`` scales the penalty; 0 reduces to the default reward.
    """
    base = log_return_reward(ctx)
    if lambda_dd <= 0.0 or len(ctx.equity_history) == 0:
        return base
    peak = float(np.max(np.concatenate([ctx.equity_history, [ctx.equity_after]])))
    if peak <= 1e-12:
        return base
    drawdown = max(0.0, 1.0 - ctx.equity_after / peak)
    return float(base - lambda_dd * drawdown)


def position_tracking_reward(
    ctx: RewardContext,
    *,
    target_position: float = 0.0,
    tracking_weight: float = 1.0,
) -> float:
    """Log return minus the squared distance to a target exposure.

    Encodes the "what matters is the position reached" philosophy: agents are
    rewarded for holding near the commanded exposure rather than for trading.
    """
    base = log_return_reward(ctx)
    deviation = ctx.position_after - target_position
    return float(base - tracking_weight * deviation * deviation)


class DecayWindow:
    """O(1) exponentially-weighted running sum over a per-instance deque.

    Stateful helper for reward shaping that must span steps; unlike shared
    class attributes, each window owns its state so parallel environment
    copies never cross-contaminate.
    """

    def __init__(self, *, half_life_steps: int = 8, max_len: int = 256):
        if half_life_steps <= 0:
            raise ValueError("half_life_steps must be positive")
        if max_len < 1:
            raise ValueError("max_len must be >= 1")
        self._decay = 0.5 ** (1.0 / float(half_life_steps))
        self._max_len = max_len
        self._values: list[float] = []

    def push(self, value: float) -> None:
        self._values.append(float(value))
        if len(self._values) > self._max_len:
            self._values.pop(0)

    def weighted_sum(self) -> float:
        total = 0.0
        weight = 1.0
        for value in reversed(self._values):
            total += weight * value
            weight *= self._decay
        return total


def decayed_unrealized_profit_reward(
    ctx: RewardContext,
    *,
    window: DecayWindow,
    current_price: float,
    entry_price: float,
    scaling: float = 1.0,
) -> float:
    """Dense reward on exponentially-decayed unrealised profit while holding.

    While a position is open the step reward tracks its mark-to-market profit
    with recency weighting; realised trades still surface through equity
    changes via :func:`log_return_reward`.  ``window`` is caller-owned state.
    """
    base = log_return_reward(ctx)
    unrealised = (current_price - entry_price) * ctx.position_after
    window.push(unrealised)
    dense = scaling * window.weighted_sum()
    return float(base + 0.01 * dense)


def event_gated_profit_reward(ctx: RewardContext, *, state: dict) -> float:
    """Reward that stays silent unless a trade moved the books.

    Sells pay the wealth delta since the last entry; entries are paid for
    improving on the last exit price.  ``state`` is a caller-owned dict that
    persists across steps (``last_entry_equity``, ``last_exit_equity``).
    """
    traded = abs(ctx.position_after - ctx.position_before) > 1e-12
    if not traded:
        return 0.0
    entered = abs(ctx.position_after) > abs(ctx.position_before)
    if entered:
        reference = state.get("last_exit_equity")
        state["last_entry_equity"] = ctx.equity_after
        if reference is None:
            return log_return_reward(ctx)
        return float(np.log(max(ctx.equity_after, 1e-12) / max(reference, 1e-12)))
    state["last_exit_equity"] = ctx.equity_after
    entry_ref = state.get("last_entry_equity", ctx.equity_before)
    return float(np.log(max(ctx.equity_after, 1e-12) / max(entry_ref, 1e-12)))


REWARD_REGISTRY: dict[str, Callable[..., RewardFunction]] = {
    "log_return": lambda **_: log_return_reward,
    "drawdown_penalised": lambda **params: lambda ctx: drawdown_penalised_reward(
        ctx, **params
    ),
    "position_tracking": lambda **params: lambda ctx: position_tracking_reward(
        ctx, **params
    ),
    "decayed_unrealized_profit": lambda **params: lambda ctx: (
        decayed_unrealized_profit_reward(ctx, **params)
    ),
    "event_gated_profit": lambda **params: lambda ctx: event_gated_profit_reward(
        ctx, **params
    ),
}

REWARD_NAMES = tuple(sorted(REWARD_REGISTRY))


def build_reward(name: str, **params) -> RewardFunction:
    """Construct a reward function from its registry name."""
    factory = REWARD_REGISTRY.get(name)
    if factory is None:
        raise ValueError(f"unknown reward {name!r}; available: {REWARD_NAMES}")
    return factory(**params)
