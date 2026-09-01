import tempfile
from pathlib import Path
from typing import Any, Dict, Sequence, cast

import numpy as np
import optuna

from trading_gym.common.config import AppConfig, SearchSpaceItem, load_app_config
from trading_gym.common.persistence import atomic_write_text
from trading_gym.experiments.no_trade_prune import OptunaNoTradePruneGate
from trading_gym.pipelines.evaluation import run_walkforward
from trading_gym.pipelines.training import train


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
) -> float:
    if config.optuna is None:
        raise ValueError("Optuna configuration missing.")

    params = _suggest_params(trial, config.optuna.search_space)
    trial_cfg = _apply_params(config, params)
    trial_cfg.training.total_timesteps = config.optuna.training_timesteps

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        trial_cfg.training.checkpoint_dir = str(tmp_path / "checkpoints")
        trial_cfg.training.tensorboard_dir = str(tmp_path / "tensorboard")
        trial_cfg.training.log_dir = str(tmp_path / "logs")

        model = train(trial_cfg)
        results = run_walkforward(
            trial_cfg, timeframe, strategy_factories=[], model=model
        )
        rl_results = results.get("RLModel", [])
        if not rl_results:
            return 0.0
        # RLTrader RLTrader.py:150-163: a trial that never trades is not
        # learning the task — prune it instead of returning a 0.0 observation
        # that poisons the sampler.
        total_trades = sum(res.trade_count for res in rl_results)
        if total_trades < config.optuna.min_trades_per_trial:
            OptunaNoTradePruneGate().checkpoint(trial, total_trades)
        sharpe_scores = [res.sharpe for res in rl_results]
        mean_sharpe = float(np.mean(sharpe_scores))
        std_sharpe = float(np.std(sharpe_scores))
        return mean_sharpe - std_sharpe


def run_optuna(
    config: AppConfig,
    timeframe: str = "5m",
) -> optuna.Study:
    if config.optuna is None:
        raise ValueError("Optuna configuration missing.")

    study = optuna.create_study(direction="maximize")
    study.optimize(
        lambda trial: _objective(trial, config, timeframe),
        n_trials=config.optuna.n_trials,
        timeout=(
            config.optuna.timeout_minutes * 60
            if config.optuna.timeout_minutes
            else None
        ),
    )
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
    return study
