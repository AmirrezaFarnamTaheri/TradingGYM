"""Matched shadow replay using one existing environment/account per policy.

No financial formulas are reimplemented: each environment owns fills, marking,
fees and financing. Results are simulated policy-window outcomes, not causal
profit attribution to an isolated signal. The factory must supply the admitted
model/feature bundle and the same execution assumptions for every policy.
"""

from dataclasses import asdict, dataclass
import math
from typing import Any, Callable, Sequence
import numpy as np
from trading_gym.agent.intelligence.decision_journal import canonical_digest


@dataclass(frozen=True)
class Specialist:
    source_id: str
    model_bundle_id: str
    policy_factory: Callable[[], Any]


@dataclass(frozen=True)
class MarketFrame:
    token: str
    decision_at: float
    input_available_at: float

    def __post_init__(self):
        if (
            not self.token
            or not all(
                math.isfinite(x) for x in (self.decision_at, self.input_available_at)
            )
            or self.input_available_at > self.decision_at
        ):
            raise ValueError("market frame is unavailable or lacks identity")


@dataclass(frozen=True)
class ShadowStep:
    source_id: str
    model_bundle_id: str
    decision_id: str
    decision_at: float
    market_token: str
    outcome_available_at: float
    target_leverage: float
    equity_before: float
    equity_after: float
    position_after: float
    turnover_quote: float
    fee_cost: float
    financing_paid: float
    terminated: bool
    truncated: bool


@dataclass(frozen=True)
class ShadowLabel:
    source_id: str
    decision_id: str
    horizon_steps: int
    available_at: float | None
    net_return: float | None
    censored: bool
    outcome_kind: str = "simulated_policy_window"


@dataclass(frozen=True)
class ShadowRun:
    steps: tuple[ShadowStep, ...]
    labels: tuple[ShadowLabel, ...]
    common_steps: int
    stopped_early: bool
    execution_contract_sha256: str

    def labels_as_of(self, timestamp: float) -> tuple[ShadowLabel, ...]:
        if not math.isfinite(timestamp):
            raise ValueError("finite label availability required")
        return tuple(
            label
            for label in self.labels
            if not label.censored and label.available_at <= timestamp
        )

    def to_dict(self):
        return {
            **asdict(self),
            "execution": "simulated_existing_environment",
            "funded_eligible": False,
            "weights": "fixed externally; no online selector fitted",
        }


def run_shadow_specialists(
    specs: Sequence[Specialist],
    *,
    env_factory: Callable[[], Any],
    market_frame: Callable[[Any], MarketFrame],
    steps: int,
    horizon_steps: int,
    seed: int = 42,
) -> ShadowRun:
    """Predict all candidates before stepping any account; never auto-reset.

    Every policy sees its own evolving account observation. Delayed labels are
    made available only after the complete declared horizon, including its own
    fills/turnover and financing. The runner never fits a selector on test data.
    """
    if (
        type(steps) is not int  # noqa: E721
        or type(horizon_steps) is not int  # noqa: E721
        or not 1 <= horizon_steps <= steps <= 100_000
        or not 1 <= len(specs) <= 32
    ):
        raise ValueError(
            "bounded non-empty roster and positive replay/horizon required"
        )
    if len({s.source_id for s in specs}) != len(specs) or any(
        not s.source_id or not s.model_bundle_id for s in specs
    ):
        raise ValueError(
            "duplicate/missing source identity is not independent evidence"
        )
    envs = []
    policies = []
    observations = []
    try:
        for spec in specs:
            env = env_factory()
            if any(
                env is other
                or getattr(env, "unwrapped", env) is getattr(other, "unwrapped", other)
                for other in envs
            ):
                raise ValueError(
                    "each specialist requires an independent environment/account"
                )
            for attr in ("engine", "breaker", "_state"):
                if hasattr(env, attr) and any(
                    getattr(env, attr) is getattr(other, attr, None) for other in envs
                ):
                    raise ValueError("specialist financial/runtime state is shared")
            if getattr(env, "live_mode", False):
                raise ValueError("shadow replay cannot use a live account")
            envs.append(env)
            policy = spec.policy_factory()
            if any(policy is other for other in policies):
                raise ValueError("policy state must be independent")
            policies.append(policy)
            observation, _ = env.reset(seed=seed)
            observations.append(observation)
        opening = [float(env.equity) for env in envs]
        if len(set(opening)) != 1 or not all(
            math.isfinite(v) and v > 0 for v in opening
        ):
            raise ValueError("specialists require equal positive initial equity")
        contracts = [
            canonical_digest(
                {
                    "environment_config": env.cfg.model_dump(mode="json"),
                    "feature_order": list(env.feature_index),
                    "center": np.asarray(env._centers).tolist(),
                    "scale": np.asarray(env._scales).tolist(),
                }
            )
            for env in envs
        ]
        if len(set(contracts)) != 1:
            raise ValueError(
                "specialists require identical execution and frozen transform contracts"
            )
        records = []
        histories = [[] for _ in specs]
        stopped = False
        previous_time = None
        for step in range(steps):
            frames = [market_frame(env) for env in envs]
            if any(frame != frames[0] for frame in frames):
                raise ValueError(
                    "specialists are not on the same admitted market frame"
                )
            frame = frames[0]
            market_rows = [
                (
                    float(env.price[env.t]),
                    float(env.price_high[env.t]),
                    float(env.price_low[env.t]),
                )
                for env in envs
            ]
            if len(set(market_rows)) != 1:
                raise ValueError("market token hides different price inputs")
            if previous_time is not None and frame.decision_at <= previous_time:
                raise ValueError("replay clock must strictly advance")
            previous_time = frame.decision_at
            actions = []
            for policy, obs in zip(policies, observations):
                output = (
                    policy.predict(obs, deterministic=True)[0]
                    if hasattr(policy, "predict")
                    else policy(obs)
                )
                values = np.asarray(output, dtype=float).reshape(-1)
                if values.size != 1 or not np.isfinite(values).all():
                    raise ValueError("specialist requires one finite signed target")
                actions.append(float(values[0]))
            for i, (spec, env, target) in enumerate(zip(specs, envs, actions)):
                before = float(env.equity)
                position_before = float(env.position)
                mark = float(env.price[env.t])
                obs, _, terminated, truncated, info = env.step(
                    np.array([target], dtype=np.float32)
                )
                after = float(env.equity)
                position = float(env.position)
                if (
                    not all(math.isfinite(x) for x in (before, after, position, mark))
                    or before <= 0
                ):
                    raise ValueError("invalid simulated account evidence")
                outcome_available_at = market_frame(env).decision_at
                if outcome_available_at < frame.decision_at:
                    raise ValueError("outcome clock moved backwards")
                record = ShadowStep(
                    spec.source_id,
                    spec.model_bundle_id,
                    f"{spec.source_id}:{step}",
                    frame.decision_at,
                    frame.token,
                    outcome_available_at,
                    target,
                    before,
                    after,
                    position,
                    abs(position - position_before) * mark,
                    float(info.get("fee_cost", 0)),
                    float(info.get("financing_paid", 0)),
                    bool(terminated),
                    bool(truncated),
                )
                records.append(record)
                histories[i].append(record)
                observations[i] = obs
                stopped = stopped or bool(terminated or truncated)
            if stopped:
                break
        labels = []
        for history in histories:
            for index, record in enumerate(history):
                end = index + horizon_steps - 1
                if end >= len(history):
                    labels.append(
                        ShadowLabel(
                            record.source_id,
                            record.decision_id,
                            horizon_steps,
                            None,
                            None,
                            True,
                        )
                    )
                else:
                    terminal = history[end]
                    labels.append(
                        ShadowLabel(
                            record.source_id,
                            record.decision_id,
                            horizon_steps,
                            terminal.outcome_available_at,
                            terminal.equity_after / record.equity_before - 1,
                            False,
                        )
                    )
        return ShadowRun(
            tuple(records),
            tuple(labels),
            len(histories[0]),
            len(histories[0]) < steps,
            contracts[0],
        )
    finally:
        for env in envs:
            env.close()
