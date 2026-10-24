"""Walk-forward evaluation utilities shared by baselines and RL agents."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd

from trading_gym.baselines.strategies import Strategy
from trading_gym.common.config import AppConfig
from trading_gym.common.persistence import atomic_write_text
from trading_gym.common.security import (
    validate_regular_file,
    verified_regular_file_snapshot,
)
from trading_gym.common.utils import load_app_config
from trading_gym.common.timeframes import periods_per_year, timeframe_seconds
from trading_gym.pipelines.metrics import (
    performance_metrics,
    validate_regular_decisions,
)
from trading_gym.pipelines.research_protocol import fold_manifest, slice_inputs
from trading_gym.pipelines.environment import (
    create_environment,
    load_environment_inputs,
)
from trading_gym.pipelines.model_bundle import (
    bundle_for_model,
    read_model_bundle,
    FrozenNormalizer,
    feature_data_digest,
)

if TYPE_CHECKING:
    from stable_baselines3 import PPO
    from trading_gym.rl.margin_trading_environment import MarginTradingEnv

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    name: str
    equity_curve: np.ndarray
    rewards: np.ndarray
    final_equity: float
    sharpe: float
    max_drawdown: float
    #: Executed position changes observed during the run — the no-trade prune
    #: gate's degeneracy signal (zero means the agent never traded).
    trade_count: int = 0
    training_data_sha256: str | None = None
    evaluation_data_sha256: str | None = None
    evidence_role: str = "selection"
    decision_indices: tuple[int, ...] = ()
    fold_manifest: dict | None = None


def _anchored_splits(
    length: int, min_train_fraction: float, n_splits: int
) -> List[Tuple[slice, slice]]:
    if isinstance(n_splits, bool) or not isinstance(n_splits, int) or n_splits < 1:
        raise ValueError("n_splits must be a positive integer")
    if not 0 < min_train_fraction < 1:
        raise ValueError("min_train_fraction must be between 0 and 1")
    train_end = int(length * min_train_fraction)
    if train_end < 2 or length - train_end < 2 * n_splits:
        raise ValueError("each training/evaluation partition needs at least two rows")
    # Include the final remainder instead of silently dropping it.
    parts = np.array_split(np.arange(train_end, length), n_splits)
    return [
        (slice(0, int(part[0])), slice(int(part[0]), int(part[-1]) + 1))
        for part in parts
    ]


def _slice_inputs(
    inputs: Dict[str, np.ndarray | pd.DataFrame], interval: slice
) -> Dict[str, np.ndarray | pd.DataFrame]:
    return slice_inputs(inputs, interval)


def _run_model(
    cfg: AppConfig,
    data: Dict[str, np.ndarray | pd.DataFrame],
    model: PPO,
    annualization_factor: float,
) -> EvaluationResult:
    logger.debug("Running RL model...")
    bundle = bundle_for_model(model)
    bundle.validate_config(cfg)
    bundle.validate_features(data["features"])
    bundle.validate_runtime()
    env = create_environment(cfg, data, training_stats=bundle.training_stats)
    try:
        model.set_env(env)

        observation, _ = env.reset(seed=cfg.seed)
        rewards: List[float] = []
        equities: List[float] = [float(env.equity)]
        terminated = False
        truncated = False
        trade_count = 0
        decision_indices: list[int] = []

        while not (terminated or truncated):
            decision_indices.append(int(env.t))
            action, _ = model.predict(observation, deterministic=True)
            previous_position = env.position
            observation, reward, terminated, truncated, _ = env.step(action)
            if env.position != previous_position:
                trade_count += 1
            rewards.append(float(reward))
            equities.append(float(env.equity))

        equity_curve = np.asarray(equities, dtype=np.float64)
        rewards_arr = np.asarray(rewards, dtype=np.float64)

        metric = performance_metrics(
            equity_curve, periods_per_year=annualization_factor
        )
        sharpe, max_drawdown = metric.sharpe, -metric.max_drawdown

        return EvaluationResult(
            name="RLModel",
            equity_curve=equity_curve,
            rewards=rewards_arr,
            final_equity=float(equity_curve[-1]),
            sharpe=sharpe,
            max_drawdown=max_drawdown,
            trade_count=trade_count,
            decision_indices=tuple(decision_indices),
        )
    finally:
        env.close()


def _run_strategy(
    env: MarginTradingEnv,
    data: Dict[str, np.ndarray | pd.DataFrame],
    strategy_factory: Callable[[], Strategy],
    annualization_factor: float,
    *,
    seed: int = 0,
) -> EvaluationResult:
    strategy = strategy_factory()
    # Legacy deterministic strategies may retain reset(). Stochastic custom
    # strategies must implement reset(seed=...) and own their RNG; reseeding
    # global NumPy state would contaminate other comparators and workers.
    reset_parameters = inspect.signature(strategy.reset).parameters
    if "seed" in reset_parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in reset_parameters.values()
    ):
        strategy_seed = int(
            np.random.SeedSequence(seed, spawn_key=(1,)).generate_state(1)[0]
        )
        strategy.reset(seed=strategy_seed)
    else:
        strategy.reset()
    logger.debug(f"Running strategy {strategy.__class__.__name__}...")

    observation, _ = env.reset(seed=seed)
    rewards: List[float] = []
    equities: List[float] = [float(env.equity)]
    terminated = False
    truncated = False
    decision_indices: list[int] = []
    trade_count = 0

    while not (terminated or truncated):
        # Use the same market row as the RL observation, including warmup and
        # seeded random episode starts. A strategy cannot inspect a future tail.
        timestep = int(env.t)
        decision_indices.append(timestep)
        history = np.array(data["prices_mid"][: timestep + 1], copy=True)
        history.setflags(write=False)
        action_value = strategy.act(timestep, history)
        if not np.isfinite(action_value):
            raise ValueError("strategy action must be finite")
        previous_position = env.position
        action = np.array([action_value], dtype=np.float32)
        observation, reward, terminated, truncated, _ = env.step(action)
        if env.position != previous_position:
            trade_count += 1
        rewards.append(float(reward))
        equities.append(float(env.equity))

    equity_curve = np.asarray(equities, dtype=np.float64)
    rewards_arr = np.asarray(rewards, dtype=np.float64)

    metric = performance_metrics(equity_curve, periods_per_year=annualization_factor)
    sharpe, max_drawdown = metric.sharpe, -metric.max_drawdown

    return EvaluationResult(
        name=strategy_factory.__name__,
        equity_curve=equity_curve,
        rewards=rewards_arr,
        final_equity=float(equity_curve[-1]),
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        trade_count=trade_count,
        decision_indices=tuple(decision_indices),
    )


def run_walkforward(
    config: AppConfig,
    timeframe: str,
    strategy_factories: Iterable[Callable[[], Strategy]],
    model=None,
    *,
    trainer: Callable | None = None,
    inputs: dict | None = None,
) -> Dict[str, List[EvaluationResult]]:
    """Evaluate explicit chronological partitions.

    ``trainer(cfg, training_inputs)`` fits a NEW model per fold. Its returned
    model must carry a bundle fitted on those exact rows. A supplied ``model``
    instead represents external training and must predate every evaluation row.
    These scores are selection evidence; a final untouched test is separate.
    """
    if model is not None and trainer is not None:
        raise ValueError("provide a fitted model or a fold trainer, not both")
    inputs = load_environment_inputs(config, timeframe) if inputs is None else inputs
    features = inputs["features"]
    if not isinstance(features.index, pd.DatetimeIndex) or features.index.tz is None:
        raise ValueError("evaluation requires timezone-aware decision timestamps")
    if not features.index.is_unique or not features.index.is_monotonic_increasing:
        raise ValueError("evaluation timestamps must be unique and ordered")
    splits = _anchored_splits(
        len(features),
        config.evaluation.walkforward.min_train_fraction,
        config.evaluation.walkforward.n_splits,
    )
    if timeframe_seconds(timeframe) != config.features.timeframes[0].bar_minutes * 60:
        raise ValueError(
            "evaluation timeframe differs from the trained base feature duration"
        )
    annualization_factor = periods_per_year(timeframe, calendar_days=365.0)
    validate_regular_decisions(
        features.index.asi8.astype(np.float64) / 1e9, timeframe_seconds(timeframe)
    )
    factories = list(strategy_factories)
    results = {factory.__name__: [] for factory in factories}
    if model is not None or trainer is not None:
        results["RLModel"] = []
    for index, (train_slice, test_slice) in enumerate(splits):
        train_slice, provenance = fold_manifest(
            inputs, train_slice, test_slice, fold=index
        )
        training_inputs = _slice_inputs(inputs, train_slice)
        evaluation_inputs = _slice_inputs(inputs, test_slice)
        train_frame = training_inputs["features"]
        test_frame = evaluation_inputs["features"]
        stats = FrozenNormalizer.fit(
            train_frame, config.features.standardization.method
        ).to_stats()
        train_id, test_id = feature_data_digest(train_frame), feature_data_digest(
            test_frame
        )
        for factory in factories:
            env = create_environment(config, evaluation_inputs, training_stats=stats)
            try:
                result = _run_strategy(
                    env,
                    evaluation_inputs,
                    factory,
                    annualization_factor,
                    seed=config.seed,
                )
            finally:
                env.close()
            result.training_data_sha256, result.evaluation_data_sha256 = (
                train_id,
                test_id,
            )
            result.fold_manifest = {
                **provenance,
                "scored_rows": [test_slice.start + i for i in result.decision_indices],
                "fitted_objects": [
                    {
                        "kind": "normalizer",
                        "source_sha256": train_id,
                        "feature_cutoff": provenance["training_feature_cutoff"],
                    }
                ],
            }
            results[factory.__name__].append(result)
        if model is None and trainer is None:
            continue
        fold_model = model
        if trainer is not None:
            fold_cfg = config.model_copy(deep=True)
            for field in ("checkpoint_dir", "tensorboard_dir", "log_dir"):
                setattr(
                    fold_cfg.training,
                    field,
                    str(Path(getattr(config.training, field)) / f"fold_{index}"),
                )
            fold_model = trainer(fold_cfg, training_inputs)
        bundle = bundle_for_model(fold_model)
        bundle.validate_config(config)
        if pd.Timestamp(bundle.training_last) >= test_frame.index[0]:
            raise ValueError("model training overlaps the evaluation interval")
        if trainer is not None and (
            bundle.training_data_sha256 != train_id
            or bundle.training_market_sha256 != provenance["training_market_sha256"]
        ):
            raise ValueError(
                "fold trainer expanded or changed its admitted training rows/market tape"
            )
        result = _run_model(config, evaluation_inputs, fold_model, annualization_factor)
        result.training_data_sha256 = bundle.training_data_sha256
        result.evaluation_data_sha256 = test_id
        result.fold_manifest = {
            **provenance,
            "scored_rows": [test_slice.start + i for i in result.decision_indices],
            "fitted_objects": [
                {
                    "kind": "model_and_normalizer",
                    "source_sha256": bundle.training_data_sha256,
                    "market_sha256": bundle.training_market_sha256,
                    "feature_cutoff": bundle.training_last,
                }
            ],
        }
        results["RLModel"].append(result)
    return results


def summarize_results(results: Dict[str, List[EvaluationResult]]) -> pd.DataFrame:
    rows = []
    for name, per_split in results.items():
        for idx, res in enumerate(per_split):
            rows.append(
                {
                    "strategy": name,
                    "split": idx,
                    "final_equity": res.final_equity,
                    "sharpe": res.sharpe,
                    "max_drawdown": res.max_drawdown,
                    "training_data_sha256": res.training_data_sha256,
                    "evaluation_data_sha256": res.evaluation_data_sha256,
                    "evidence_role": res.evidence_role,
                }
            )
    return pd.DataFrame(rows)


def run_from_cli(
    config_path: str | Path,
    timeframe: str,
    model_path: str | Path,
    model_sha256: str,
    output_path: str | Path | None = None,
) -> pd.DataFrame:
    from stable_baselines3 import PPO

    cfg = load_app_config(config_path)
    artifact = validate_regular_file(
        model_path, label="model", max_bytes=8 * 1024 * 1024 * 1024
    )
    with verified_regular_file_snapshot(
        artifact,
        expected_sha256=model_sha256,
        label="model",
        max_bytes=8 * 1024 * 1024 * 1024,
    ) as snapshot:
        bundle = read_model_bundle(snapshot, expected_model_sha256=model_sha256)
        bundle.validate_config(cfg)
        bundle.validate_runtime()
        model = PPO.load(snapshot, device=cfg.training.device)
    results = run_walkforward(cfg, timeframe, strategy_factories=[], model=model)
    summary = summarize_results(results)
    if output_path:
        atomic_write_text(
            Path(output_path).expanduser(),
            summary.to_csv(index=False),
            max_bytes=64 * 1024 * 1024,
            mode=0o600,
        )
    return summary
