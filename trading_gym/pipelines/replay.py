"""One uninterrupted policy episode; financial transitions remain in the native env."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class EpisodeTrace:
    equity: tuple[float, ...]
    rewards: tuple[float, ...]
    positions: tuple[float, ...]
    decision_indices: tuple[int, ...]
    terminated: bool
    truncated: bool


def replay_episode(model, env, *, seed: int, max_steps: int) -> EpisodeTrace:
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("max_steps must be a positive integer")
    observation, _ = env.reset(seed=seed)
    equities, rewards, positions, indices = [float(env.equity)], [], [], []
    terminated = truncated = False
    for _ in range(max_steps):
        indices.append(int(env.t))
        action, _ = model.predict(observation, deterministic=True)
        observation, reward, terminated, truncated, _ = env.step(action)
        equities.append(float(env.equity))
        rewards.append(float(reward))
        positions.append(float(env.position))
        if terminated or truncated:
            break  # A reset is a separate account experiment, never one return.
    if not np.isfinite(equities).all() or not np.isfinite(rewards).all():
        raise ValueError("episode produced nonfinite accounting evidence")
    return EpisodeTrace(tuple(equities), tuple(rewards), tuple(positions), tuple(indices),
                        bool(terminated), bool(truncated))
