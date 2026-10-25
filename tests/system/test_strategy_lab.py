from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading_gym.agent.strategy_lab.evaluator import evaluate_strategy
from trading_gym.agent.strategy_lab.models import StrategySpec
from trading_gym.agent.strategy_lab.overfitting import (
    bootstrap_sharpe_interval,
    probabilistic_sharpe_ratio,
    purged_walk_forward,
)
from trading_gym.agent.strategy_lab.repository import (
    StrategyConflictError,
    StrategyRepository,
)
from trading_gym.agent.strategy_lab.templates import template_catalogue


def market_frame(rows: int = 200) -> pd.DataFrame:
    close = np.linspace(100, 140, rows) + np.sin(np.arange(rows) / 4)
    return pd.DataFrame(
        {
            "open": close - 0.2,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.linspace(1000, 2000, rows),
        }
    )


def test_templates_are_valid_and_evaluable() -> None:
    for spec in template_catalogue().values():
        validated = StrategySpec.model_validate(spec.model_dump())
        result = evaluate_strategy(market_frame(), validated)
        assert {"entry_signal", "exit_signal", "position_intent"}.issubset(
            result.columns
        )
        assert set(result["position_intent"].unique()).issubset({-1, 0, 1})


def test_strategy_schema_rejects_arbitrary_code() -> None:
    payload = template_catalogue()["sma_crossover"].model_dump()
    payload["python"] = "__import__('os').system('whoami')"
    with pytest.raises(Exception):
        StrategySpec.model_validate(payload)


def test_strategy_repository_uses_revision_cas(tmp_path: Path) -> None:
    repository = StrategyRepository(tmp_path / "strategies.json")
    spec = template_catalogue()["sma_crossover"]
    first = repository.save(spec, actor="test")
    second = repository.save(
        spec, strategy_id=first["strategy_id"], expected_revision=1, actor="test"
    )
    assert second["revision"] == 2
    with pytest.raises(StrategyConflictError):
        repository.save(
            spec, strategy_id=first["strategy_id"], expected_revision=1, actor="stale"
        )


def test_overfitting_controls_are_deterministic_and_purged() -> None:
    folds = purged_walk_forward(
        200, train_size=60, test_size=20, purge_size=5, embargo_size=2
    )
    assert folds
    assert all(fold.train_end + 5 == fold.test_start for fold in folds)
    returns = np.array([0.01, -0.004, 0.006, 0.002, -0.001] * 20)
    assert 0 <= probabilistic_sharpe_ratio(returns) <= 1
    first = bootstrap_sharpe_interval(returns, samples=200, seed=42)
    second = bootstrap_sharpe_interval(returns, samples=200, seed=42)
    assert first == second
