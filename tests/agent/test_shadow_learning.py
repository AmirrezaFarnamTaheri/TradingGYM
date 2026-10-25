import numpy as np
import pytest
from filelock import FileLock, Timeout
from trading_gym.agent.daemon import AgentSettings
from trading_gym.agent.shadow_learning import (
    ShadowLearner,
    ShadowMarketEnv,
    observation,
)


def test_proxy_costs_and_observation_are_finite():
    settings = AgentSettings(shadow_learning_enabled=False)
    env = ShadowMarketEnv([100.0] * 40, settings)
    before = env.equity
    _, reward, _, _, _ = env.step(np.array([1.0]))
    assert reward < 0 and env.equity < before
    assert np.isfinite(observation([100.0, 101.0])).all()


def test_real_cpu_training_keeps_holdout_separate(tmp_path):
    learner = ShadowLearner(tmp_path)
    settings = AgentSettings(
        symbols=["BTCUSDT"], shadow_training_steps=128, shadow_learning_enabled=False
    )
    bars = [(1704067200 + i * 300, 100 + np.sin(i / 10) + i * 0.01) for i in range(320)]
    result = learner.train_and_evaluate(bars, "BTCUSDT", settings)
    assert result["training_bars"] == 224
    assert result["holdout_bars"] == 96
    assert result["scope"] == "paper_only"
    assert learner.path.is_file()
    with pytest.raises(ValueError, match="fresh"):
        learner.train_and_evaluate(bars, "BTCUSDT", settings)


def test_concurrent_training_is_excluded(tmp_path):
    learner = ShadowLearner(tmp_path)
    with FileLock(str(tmp_path / "training.lock")):
        with pytest.raises(Timeout):
            learner.train_and_evaluate([], "BTCUSDT", AgentSettings())
