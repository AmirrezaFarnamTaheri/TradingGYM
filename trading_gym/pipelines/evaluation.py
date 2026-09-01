"""Walk-forward evaluation utilities shared by baselines and RL agents."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd
from stable_baselines3 import PPO

from trading_gym.baselines.strategies import Strategy
from trading_gym.common.config import AppConfig
from trading_gym.common.persistence import atomic_write_text
from trading_gym.common.security import validate_regular_file, verified_regular_file_snapshot
from trading_gym.common.utils import load_app_config
from trading_gym.pipelines.environment import (
    create_environment,
    load_environment_inputs,
)
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


def _anchored_splits(
    length: int, min_train_fraction: float, n_splits: int
) -> List[Tuple[slice, slice]]:
    if not 0 < min_train_fraction < 1:
        raise ValueError("min_train_fraction must be between 0 and 1.")
    train_end = max(int(length * min_train_fraction), 1)
    test_length = max((length - train_end) // n_splits, 1)

    splits: List[Tuple[slice, slice]] = []
    for split in range(n_splits):
        train_slice = slice(0, train_end + split * test_length)
        test_start = train_slice.stop
        test_end = min(length, train_slice.stop + test_length)
        if test_start >= length:
            break
        splits.append((train_slice, slice(test_start, test_end)))
    return splits


def _slice_inputs(
    inputs: Dict[str, np.ndarray | pd.DataFrame], interval: slice
) -> Dict[str, np.ndarray | pd.DataFrame]:
    sliced = {}
    for key, value in inputs.items():
        if isinstance(value, pd.DataFrame):
            sliced[key] = value.iloc[interval]
        else:
            sliced[key] = value[interval]
    return sliced


def _run_model(
    cfg: AppConfig,
    data: Dict[str, np.ndarray | pd.DataFrame],
    model: PPO,
    annualization_factor: float,
) -> EvaluationResult:
    logger.debug("Running RL model...")
    env = create_environment(cfg, data)
    model.set_env(env)

    observation, _ = env.reset()
    rewards: List[float] = []
    equities: List[float] = [float(env.equity)]
    terminated = False
    truncated = False
    trade_count = 0

    while not (terminated or truncated):
        action, _ = model.predict(observation, deterministic=True)
        previous_position = env.position
        observation, reward, terminated, truncated, _ = env.step(action)
        if env.position != previous_position:
            trade_count += 1
        rewards.append(float(reward))
        equities.append(float(env.equity))

    equity_curve = np.asarray(equities, dtype=np.float64)
    rewards_arr = np.asarray(rewards, dtype=np.float64)

    # THEORETICAL FIX: Sharpe ratio should be calculated from actual returns,
    # not from reward signals. The reward is a transformed/scaled value that
    # doesn't represent true portfolio returns.
    # Sharpe = (mean(returns) - risk_free_rate) / std(returns) * sqrt(annualization)
    # We assume risk_free_rate ≈ 0 for crypto markets.
    if equity_curve.size > 1:
        # Calculate percentage returns from equity curve
        returns = np.diff(equity_curve) / np.maximum(equity_curve[:-1], 1e-12)
        mean_return = float(np.mean(returns))
        std_return = float(np.std(returns))
        sharpe = (
            mean_return / (std_return + 1e-12) * np.sqrt(annualization_factor)
            if std_return > 1e-12
            else 0.0
        )
    else:
        sharpe = 0.0

    peak = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peak) / np.maximum(peak, 1e-12)
    max_drawdown = float(np.min(drawdown)) if drawdown.size else 0.0

    return EvaluationResult(
        name="RLModel",
        equity_curve=equity_curve,
        rewards=rewards_arr,
        final_equity=float(equity_curve[-1]),
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        trade_count=trade_count,
    )


def _run_strategy(
    env: MarginTradingEnv,
    data: Dict[str, np.ndarray | pd.DataFrame],
    strategy_factory: Callable[[], Strategy],
    annualization_factor: float,
) -> EvaluationResult:
    strategy = strategy_factory()
    strategy.reset()
    logger.debug(f"Running strategy {strategy.__class__.__name__}...")

    observation, _ = env.reset()
    rewards: List[float] = []
    equities: List[float] = [float(env.equity)]
    terminated = False
    truncated = False
    timestep = 0

    while not (terminated or truncated):
        # The `act` method for baselines might expect a simple price array
        action_value = strategy.act(timestep, data["prices_mid"])
        action = np.array([action_value], dtype=np.float32)
        observation, reward, terminated, truncated, _ = env.step(action)
        rewards.append(float(reward))
        equities.append(float(env.equity))
        timestep += 1

    equity_curve = np.asarray(equities, dtype=np.float64)
    rewards_arr = np.asarray(rewards, dtype=np.float64)

    # THEORETICAL FIX: Sharpe ratio from actual returns, not rewards
    if equity_curve.size > 1:
        returns = np.diff(equity_curve) / np.maximum(equity_curve[:-1], 1e-12)
        mean_return = float(np.mean(returns))
        std_return = float(np.std(returns))
        sharpe = (
            mean_return / (std_return + 1e-12) * np.sqrt(annualization_factor)
            if std_return > 1e-12
            else 0.0
        )
    else:
        sharpe = 0.0

    peak = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peak) / np.maximum(peak, 1e-12)
    max_drawdown = float(np.min(drawdown)) if drawdown.size else 0.0

    return EvaluationResult(
        name=strategy_factory.__name__,
        equity_curve=equity_curve,
        rewards=rewards_arr,
        final_equity=float(equity_curve[-1]),
        sharpe=sharpe,
        max_drawdown=max_drawdown,
    )


def run_walkforward(
    config: AppConfig,
    timeframe: str,
    strategy_factories: Iterable[Callable[[], Strategy]],
    model: PPO | None = None,
) -> Dict[str, List[EvaluationResult]]:
    logger.info(f"Starting walk-forward evaluation for timeframe '{timeframe}'...")
    logger.info("Loading environment inputs...")
    inputs = load_environment_inputs(config, timeframe)
    data_length = len(inputs["prices_mid"])
    logger.info(f"Loaded {data_length} data points.")

    logger.info("Generating walk-forward splits...")
    splits = _anchored_splits(
        data_length,
        config.evaluation.walkforward.min_train_fraction,
        config.evaluation.walkforward.n_splits,
    )
    logger.info(f"Generated {len(splits)} splits for evaluation.")

    timeframe_minutes = int(timeframe.replace("m", ""))
    annualization_factor = 252 * 24 * 60 / timeframe_minutes

    results: Dict[str, List[EvaluationResult]] = {}

    for factory in strategy_factories:
        name = factory.__name__
        results[name] = []
        logger.info(f"Evaluating strategy: {name}")
        for i, (_, test_slice) in enumerate(splits):
            logger.info(f"Running split {i+1}/{len(splits)} for strategy {name}...")
            sliced_inputs = _slice_inputs(inputs, test_slice)
            env = create_environment(config, sliced_inputs)
            result = _run_strategy(env, sliced_inputs, factory, annualization_factor)
            results[name].append(result)
            logger.info(
                f"Split {i+1} results for {name}: Sharpe={result.sharpe:.4f}, "
                f"Final Equity={result.final_equity:.2f}"
            )

    if model is not None:
        model_results: List[EvaluationResult] = []
        logger.info("Evaluating RL model...")
        for i, (_, test_slice) in enumerate(splits):
            logger.info(f"Running split {i+1}/{len(splits)} for RL model...")
            sliced_inputs = _slice_inputs(inputs, test_slice)
            result = _run_model(config, sliced_inputs, model, annualization_factor)
            model_results.append(result)
            logger.info(
                f"Split {i+1} results for RL model: Sharpe={result.sharpe:.4f}, "
                f"Final Equity={result.final_equity:.2f}"
            )
        results["RLModel"] = model_results

    logger.info("Walk-forward evaluation finished.")
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
