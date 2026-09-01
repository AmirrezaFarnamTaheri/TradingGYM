import numpy as np
import pandas as pd
import pytest

from trading_gym.baselines.strategies import BuyAndHoldStrategy
from trading_gym.pipelines import evaluation


class _StubEnv:
    def __init__(self, length: int, initial_equity: float) -> None:
        self.length = length
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self._step = 0

    def reset(self):
        self._step = 0
        self.equity = self.initial_equity
        return np.array([0.0]), {}

    def step(self, action, **kwargs):
        del action, kwargs
        self._step += 1
        terminated = self._step >= self.length
        truncated = False
        return np.array([0.0]), 0.0, terminated, truncated, {}


@pytest.fixture
def synthetic_inputs() -> dict:
    length = 50
    close = np.linspace(100.0, 110.0, length)
    spread = np.full(length, 10.0)
    caps = np.full(length, 100_000.0)
    vol = np.full(length, 0.01)
    missing = np.zeros(length, dtype=np.int8)
    return {
        "close": close,
        "spread_bps": spread,
        "buy_cap": caps,
        "sell_cap": caps,
        "vol_hint": vol,
        "is_missing": missing,
    }


def test_run_walkforward_with_mocked_inputs(monkeypatch, synthetic_inputs, app_config):
    cfg = app_config
    cfg.evaluation.walkforward.n_splits = 2
    cfg.evaluation.walkforward.min_train_fraction = 0.5
    cfg.training.initial_equity = 1_000.0

    def fake_load_inputs(*args, **kwargs):
        del args, kwargs
        # Return a structure that matches the real load_environment_inputs
        return {
            "features": pd.DataFrame(synthetic_inputs["close"]),
            "prices_mid": synthetic_inputs["close"],
            "prices_high": synthetic_inputs["close"],
            "prices_low": synthetic_inputs["close"],
        }

    def fake_create_environment(_cfg, data):
        del data
        return _StubEnv(
            length=len(synthetic_inputs["close"]),
            initial_equity=cfg.training.initial_equity,
        )

    monkeypatch.setattr(evaluation, "load_environment_inputs", fake_load_inputs)
    monkeypatch.setattr(evaluation, "create_environment", fake_create_environment)

    results = evaluation.run_walkforward(
        cfg, "5m", [lambda: BuyAndHoldStrategy(exposure=0.0)], model=None
    )
    assert len(results) == 1
    strategy_key = next(iter(results))
    assert len(results[strategy_key]) == 2
    for res in results[strategy_key]:
        assert res.final_equity == pytest.approx(cfg.training.initial_equity)
