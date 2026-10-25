import numpy as np
import pandas as pd
import pytest

from trading_gym.rl.baselines.strategies import BuyAndHoldStrategy
from trading_gym.rl.pipelines import evaluation


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
            "features": pd.DataFrame(
                {
                    "spread_ratio": synthetic_inputs["spread_bps"] / 10000,
                    "volatility": synthetic_inputs["vol_hint"],
                },
                index=pd.date_range(
                    "2025-01-01",
                    periods=len(synthetic_inputs["close"]),
                    freq="5min",
                    tz="UTC",
                ),
            ),
            "prices_mid": synthetic_inputs["close"],
            "prices_high": synthetic_inputs["close"],
            "prices_low": synthetic_inputs["close"],
        }

    monkeypatch.setattr(evaluation, "load_environment_inputs", fake_load_inputs)

    results = evaluation.run_walkforward(
        cfg, "5m", [lambda: BuyAndHoldStrategy(exposure=0.0)], model=None
    )
    assert len(results) == 1
    strategy_key = next(iter(results))
    assert len(results[strategy_key]) == 2
    for res in results[strategy_key]:
        assert res.final_equity == pytest.approx(cfg.training.initial_equity)
