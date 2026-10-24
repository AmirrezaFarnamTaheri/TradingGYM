"""Real environment comparators share seeded starts and causally visible rows."""
from types import SimpleNamespace

import numpy as np
import pandas as pd

from trading_gym.common.config import load_app_config
from trading_gym.pipelines.environment import create_environment
from trading_gym.pipelines.evaluation import _run_model, _run_strategy
from trading_gym.pipelines.model_bundle import attach_model_bundle, fit_model_bundle


def test_real_env_baseline_and_policy_use_same_nonzero_decision_rows():
    cfg = load_app_config("config/app.yaml")
    cfg.simulator.steps = 5
    cfg.env.breaker.stall_check_enabled = False
    n = 260
    index = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    features = pd.DataFrame({"spread_ratio": np.full(n, .0001),
                             "volatility": np.full(n, .001)}, index=index)
    prices = np.arange(n, dtype=float) + 100
    inputs = {"features": features, "prices_mid": prices,
              "prices_high": prices, "prices_low": prices}
    bundle = fit_model_bundle(cfg, features)
    env = create_environment(cfg, inputs, training_stats=bundle.training_stats)
    seen = []

    class Recorder:
        def reset(self):
            pass

        def act(self, t, history):
            assert len(history) == t + 1
            assert history[-1] == prices[t]
            assert not history.flags.writeable
            seen.append(t)
            return 0.

    model = SimpleNamespace(set_env=lambda value: None,
                            predict=lambda obs, deterministic: (np.array([0.], dtype=np.float32), None))
    attach_model_bundle(model, bundle)
    try:
        baseline = _run_strategy(env, inputs, Recorder, 365 * 288, seed=cfg.seed)
        policy = _run_model(cfg, inputs, model, 365 * 288)
    finally:
        env.close()
    assert baseline.decision_indices == policy.decision_indices == tuple(seen)
    assert baseline.decision_indices[0] >= 200
    assert len(seen) == 5
    np.testing.assert_array_equal(baseline.equity_curve, policy.equity_curve)
    assert baseline.trade_count == policy.trade_count == 0
