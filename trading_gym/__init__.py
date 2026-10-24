"""Public SDK for the Trading Gym project.

Heavy RL dependencies are imported lazily so governance, research, strategy, and
agent tooling remain usable in constrained operator and API environments.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from trading_gym.common.config import AppConfig, load_app_config
from trading_gym.common.logging import configure_logging, configure_logging_from_env


def make(
    config: Union[str, Path, AppConfig],
    timeframe: str = "5m",
    training_stats: Optional[Dict[str, Any]] = None,
):
    """Create an environment with explicitly supplied frozen training statistics.

    Pass an admitted model bundle's ``training_stats`` for evaluation/operation.
    New training callers fit statistics on their designated training partition.
    This function never estimates preprocessing from the data being evaluated.
    """
    if training_stats is None:
        raise ValueError(
            "frozen training_stats required; use a model bundle or fit a designated training partition"
        )

    from trading_gym.pipelines.environment import (
        create_environment,
        load_environment_inputs,
    )

    cfg = load_app_config(config) if not isinstance(config, AppConfig) else config
    inputs = load_environment_inputs(cfg, timeframe)
    return create_environment(cfg, inputs, training_stats=training_stats)


__all__ = [
    "AppConfig",
    "configure_logging",
    "configure_logging_from_env",
    "load_app_config",
    "make",
]
