import tempfile
import json
from pathlib import Path
from typing import Any, Dict, Sequence, cast

import numpy as np
import optuna

from trading_gym.core.config import AppConfig, SearchSpaceItem, load_app_config
from trading_gym.core.persistence import atomic_write_text
from trading_gym.rl.experiments.no_trade_prune import (
    NoTradePrunePolicy,
    OptunaNoTradePruneGate,
)
from trading_gym.rl.pipelines.evaluation import run_walkforward
from trading_gym.rl.pipelines.training import train
from trading_gym.rl.pipelines.environment import load_environment_inputs
from trading_gym.rl.pipelines.research_protocol import reserve_final_segment


def _suggest_params(
    trial: optuna.Trial, space: Dict[str, SearchSpaceItem]
) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    for name, spec in space.items():
        if spec.type == "float":
            if spec.low is None or spec.high is None:
                raise ValueError(f"Search space '{name}' missing bounds.")
            params[name] = trial.suggest_float(
                name, float(spec.low), float(spec.high), log=bool(spec.log)
            )
        elif spec.type == "categorical":
            if not spec.choices:
                raise ValueError(f"Search space '{name}' missing categorical choices.")
            params[name] = trial.suggest_categorical(
                name, list(cast(Sequence[Any], spec.choices))
            )
        else:
            raise ValueError(f"Unsupported search space type {spec.type} for {name}")
    return params


def _apply_params(cfg: AppConfig, params: Dict[str, Any]) -> AppConfig:
    updated = cfg.model_copy(deep=True)
    for key, value in params.items():
        if hasattr(updated.training.ppo, key):
            setattr(updated.training.ppo, key, value)
    return updated


def _objective(
    trial: optuna.Trial,
    config: AppConfig,
    timeframe: str,
    *,
    inputs: dict | None = None,
    protocol_id: str | None = None,
) -> float:
    if config.optuna is None:
        raise ValueError("Optuna configuration missing.")

    if protocol_id is not None:
        trial.set_user_attr("protocol_id", protocol_id)
    params = _suggest_params(trial, config.optuna.search_space)
    trial_cfg = _apply_params(config, params)
    trial_cfg.training.total_timesteps = config.optuna.training_timesteps

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        trial_cfg.training.checkpoint_dir = str(tmp_path / "checkpoints")
        trial_cfg.training.tensorboard_dir = str(tmp_path / "tensorboard")
        trial_cfg.training.log_dir = str(tmp_path / "logs")

        results = run_walkforward(
            trial_cfg,
            timeframe,
            strategy_factories=[],
            inputs=inputs,
            trainer=lambda fold_cfg, training_inputs: train(
                fold_cfg, inputs=training_inputs, timeframe=timeframe
            ),
        )
        rl_results = results.get("RLModel", [])
        if not rl_results:
            raise optuna.TrialPruned("no valid evaluation folds")
        # RLTrader RLTrader.py:150-163: a trial that never trades is not
        # learning the task — prune it instead of returning a 0.0 observation
        # that poisons the sampler.
        total_trades = sum(res.trade_count for res in rl_results)
        if total_trades < config.optuna.min_trades_per_trial:
            OptunaNoTradePruneGate(
                NoTradePrunePolicy(min_trades=config.optuna.min_trades_per_trial)
            ).checkpoint(trial, total_trades)
        sharpe_scores = [res.sharpe for res in rl_results]
        if not np.isfinite(sharpe_scores).all():
            raise optuna.TrialPruned("nonfinite fold score")
        trial.set_user_attr(
            "folds",
            [
                {
                    "training_data_sha256": res.training_data_sha256,
                    "evaluation_data_sha256": res.evaluation_data_sha256,
                    "sharpe": float(res.sharpe),
                    "evidence_role": res.evidence_role,
                    "manifest": res.fold_manifest,
                }
                for res in rl_results
            ],
        )
        mean_sharpe = float(np.mean(sharpe_scores))
        std_sharpe = float(np.std(sharpe_scores))
        score = mean_sharpe - std_sharpe
        if not np.isfinite(score):
            raise optuna.TrialPruned("nonfinite aggregate score")
        return score


def run_optuna(
    config: AppConfig,
    timeframe: str = "5m",
    *,
    final_fraction: float = 0.2,
    inputs: dict | None = None,
) -> optuna.Study:
    if config.optuna is None:
        raise ValueError("Optuna configuration missing.")

    # Reserve final evidence BEFORE any candidate is fit or scored. Every
    # candidate receives the same fixed development snapshot.
    admitted = load_environment_inputs(config, timeframe) if inputs is None else inputs
    protocol, development = reserve_final_segment(
        admitted, candidate_budget=config.optuna.n_trials, final_fraction=final_fraction
    )
    study = optuna.create_study(
        direction="maximize", sampler=optuna.samplers.TPESampler(seed=config.seed)
    )
    study.set_user_attr("protocol", protocol.specification)
    study.set_user_attr("protocol_id", protocol.seal_id)
    try:
        study.optimize(
            lambda trial: _objective(
                trial,
                config,
                timeframe,
                inputs=development,
                protocol_id=protocol.seal_id,
            ),
            n_trials=config.optuna.n_trials,
            timeout=(
                config.optuna.timeout_minutes * 60
                if config.optuna.timeout_minutes
                else None
            ),
        )
    finally:
        study.set_user_attr("actual_candidate_count", len(study.trials))
        study.set_user_attr("final_outcomes_consumed", False)

    return study


def run_from_cli(
    config_path: str | Path,
    timeframe: str = "5m",
    output_path: str | Path | None = None,
) -> optuna.Study:
    cfg = load_app_config(config_path)
    study = run_optuna(cfg, timeframe)
    if output_path:
        df = study.trials_dataframe()
        serialised = df.to_csv(index=False)
        atomic_write_text(
            Path(output_path).expanduser(),
            serialised,
            max_bytes=64 * 1024 * 1024,
            mode=0o600,
        )
        report_path = Path(output_path).expanduser()
        atomic_write_text(
            report_path.with_name(f"{report_path.stem}.protocol.json"),
            json.dumps(study.user_attrs, sort_keys=True, indent=2, allow_nan=False),
            max_bytes=2 * 1024 * 1024,
            mode=0o600,
        )
    return study
