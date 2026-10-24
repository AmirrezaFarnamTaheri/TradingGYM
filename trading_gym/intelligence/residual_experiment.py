"""Bounded residual experiment shared by training and shadow evaluation.

The residual changes a target, not permission. This module is never selected
by the funded-live factory. A residual policy must be trained on the same
composition used at evaluation; adding a residual after full-target training
is a different, unqualified policy.
"""

from dataclasses import dataclass
import math
from numbers import Real
from typing import Any, Callable
import numpy as np
from trading_gym.intelligence.shadow_specialists import (
    Specialist,
    run_shadow_specialists,
)


@dataclass(frozen=True)
class ResidualComposition:
    baseline_target: float
    requested_residual: float
    bounded_residual: float
    final_target: float
    applied_residual: float


def compose_residual(
    baseline: float,
    residual: float,
    *,
    residual_bound: float,
    action_low: float,
    action_high: float,
    allow_direction_change: bool = False,
    allow_open_from_flat: bool = False,
) -> ResidualComposition:
    values = (baseline, residual, residual_bound, action_low, action_high)
    if any(
        not isinstance(v, Real) or isinstance(v, bool) or not math.isfinite(v)
        for v in values
    ):
        raise ValueError("finite real target/residual/bounds required")
    if (
        residual_bound <= 0
        or not action_low <= 0 <= action_high
        or action_low >= action_high
        or not action_low <= baseline <= action_high
    ):
        raise ValueError("invalid baseline or action bounds")
    if (
        type(allow_direction_change) is not bool  # noqa: E721
        or type(allow_open_from_flat) is not bool  # noqa: E721
    ):
        raise ValueError("residual permissions must be explicit booleans")
    bounded = max(-residual_bound, min(residual_bound, residual))
    target = max(action_low, min(action_high, baseline + bounded))
    if baseline == 0 and not allow_open_from_flat:
        target = 0.0
    if not allow_direction_change and baseline * target < 0:
        target = 0.0
    return ResidualComposition(
        float(baseline),
        float(residual),
        float(bounded),
        float(target),
        float(target - baseline),
    )


def _target(policy: Any, observation: Any) -> float:
    result = (
        policy.predict(observation, deterministic=True)[0]
        if hasattr(policy, "predict")
        else policy(observation)
    )
    raw = np.asarray(result)
    if raw.dtype.kind not in "iuf":
        raise ValueError("policy target must be a real number")
    value = raw.astype(float).ravel()
    if value.size != 1 or not np.isfinite(value).all():
        raise ValueError("one finite policy target required")
    return float(value[0])


class ResidualPolicy:
    """Stateless SB3-shaped evaluation adapter for a trained residual model."""

    def __init__(self, baseline: Any, residual: Any, **composition):
        self.baseline, self.residual, self.composition = baseline, residual, composition
        self.last_composition = None

    def predict(self, observation, deterministic=True):
        if not deterministic:
            raise ValueError("qualification uses deterministic predictions")
        self.last_composition = compose_residual(
            _target(self.baseline, observation),
            _target(self.residual, observation),
            **self.composition,
        )
        return np.array([self.last_composition.final_target], dtype=np.float32), None


class _FixedAverage:
    """Explicit 50/50 target comparator; no probabilistic confidence claim."""

    def __init__(self, baseline, rl):
        self.baseline, self.rl = baseline, rl

    def predict(self, observation, deterministic=True):
        return (
            np.array(
                [
                    0.5
                    * (
                        _target(self.baseline, observation)
                        + _target(self.rl, observation)
                    )
                ],
                dtype=np.float32,
            ),
            None,
        )


def compare_residual_candidates(
    *,
    baseline_factory: Callable[[], Any],
    rl_factory: Callable[[], Any],
    residual_factory: Callable[[], Any],
    bundle_ids: dict[str, str],
    composition: dict[str, Any],
    **replay,
):
    """Baseline/full-target RL/fixed-average/residual each get their own account.

    The full-target RL and delta-trained residual model are different artifacts.
    No winner is selected or retrained on these returned evaluation outcomes.
    """
    if set(bundle_ids) != {"baseline", "rl", "residual"}:
        raise ValueError("three explicit model bundle identities required")
    specs = [
        Specialist("baseline", bundle_ids["baseline"], baseline_factory),
        Specialist("rl-only", bundle_ids["rl"], rl_factory),
        Specialist(
            "fixed-average",
            f"{bundle_ids['baseline']}+{bundle_ids['rl']}",
            lambda: _FixedAverage(baseline_factory(), rl_factory()),
        ),
        Specialist(
            "residual",
            f"{bundle_ids['baseline']}+{bundle_ids['residual']}",
            lambda: ResidualPolicy(
                baseline_factory(), residual_factory(), **composition
            ),
        ),
    ]
    return run_shadow_specialists(specs, **replay)


def residual_training_environment(
    env,
    baseline: Any,
    *,
    residual_bound: float,
    allow_direction_change: bool = False,
    allow_open_from_flat: bool = False,
):
    """Wrap the existing accounting env; PPO actions become bounded deltas.

    Gymnasium is an optional experiment dependency. The wrapper is stateless
    apart from the current observation; recurrent baseline/residual export is
    deliberately outside this first experiment's supported contract.
    """
    import gymnasium as gym

    if not math.isfinite(residual_bound) or residual_bound <= 0:
        raise ValueError("positive residual bound required")
    if getattr(env, "live_mode", False):
        raise ValueError("residual training requires an isolated offline account")

    class ResidualTrainingEnv(gym.Wrapper):
        def __init__(self):
            super().__init__(env)
            self.action_space = gym.spaces.Box(
                -residual_bound, residual_bound, shape=(1,), dtype=np.float32
            )
            self._observation = None
            self.contract = dict(
                residual_bound=residual_bound,
                action_low=float(env.action_space.low[0]),
                action_high=float(env.action_space.high[0]),
                allow_direction_change=allow_direction_change,
                allow_open_from_flat=allow_open_from_flat,
            )

        def reset(self, **kwargs):
            self._observation, info = self.env.reset(**kwargs)
            return self._observation, info

        def step(self, action):
            if self._observation is None:
                raise RuntimeError("reset before residual step")
            value = np.asarray(action, dtype=float).ravel()
            if value.size != 1:
                raise ValueError("one residual action required")
            composition = compose_residual(
                _target(baseline, self._observation), float(value[0]), **self.contract
            )
            self._observation, reward, terminated, truncated, info = self.env.step(
                np.array([composition.final_target], dtype=np.float32)
            )
            return (
                self._observation,
                reward,
                terminated,
                truncated,
                {**info, "residual_composition": composition.__dict__},
            )

    return ResidualTrainingEnv()
