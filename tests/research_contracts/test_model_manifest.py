"""Actual CPU PPO training/save/load plus non-executable manifest inspection."""
import hashlib
import json

import numpy as np
import pandas as pd
import pytest
import torch
from stable_baselines3 import PPO

import trading_gym
from trading_gym.common.config import load_app_config
from trading_gym.pipelines import environment
from trading_gym.pipelines.model_bundle import (
    ModelBundle, bundle_for_model, fit_model_bundle, inspect_model_bundle, read_model_bundle,
)
from trading_gym.pipelines.training import train


def sample_inputs():
    n = 260
    index = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    features = pd.DataFrame({"spread_ratio": np.full(n, .0002),
                             "volatility": np.full(n, .001)}, index=index)
    prices = 100 + .05 * np.sin(np.arange(n) / 10.)
    return {"features": features, "prices_mid": prices, "prices_high": prices,
            "prices_low": prices}


def test_manifest_pins_action_economics_units_and_runtime():
    cfg, inputs = load_app_config("config/app.yaml"), sample_inputs()
    bundle = fit_model_bundle(cfg, inputs["features"], inputs=inputs, source_revision="a" * 40)
    assert bundle.contract["action"]["zero"] == "target_flat"
    assert bundle.contract["account_features"][-1]["expression"] == "position/(initial_equity/initial_price)"
    assert bundle.training_market_sha256
    bundle.validate_runtime()
    payload = bundle.to_dict()
    payload["runtime_versions"]["torch"] = "0.0-unqualified"
    with pytest.raises(ValueError, match="compatibility qualification"):
        ModelBundle.from_dict(payload).validate_runtime()
    changed = cfg.model_copy(deep=True)
    changed.env.costs.taker_fee_bps += 1
    with pytest.raises(ValueError, match="semantics differ"):
        bundle.validate_config(changed)
    custom = inputs["features"].assign(custom_measure=1.)
    with pytest.raises(ValueError, match="unit metadata"):
        fit_model_bundle(cfg, custom)
    assert fit_model_bundle(cfg, custom, unit_overrides={"custom_measure": "dimensionless"}).contract


def test_sdk_keeps_api_but_requires_explicit_frozen_statistics(monkeypatch):
    cfg, inputs = load_app_config("config/app.yaml"), sample_inputs()
    with pytest.raises(ValueError, match="frozen training_stats"):
        trading_gym.make(cfg)
    monkeypatch.setattr(environment, "load_environment_inputs", lambda *args: inputs)
    bundle = fit_model_bundle(cfg, inputs["features"])
    env = trading_gym.make(cfg, "5m", training_stats=bundle.training_stats)
    try:
        np.testing.assert_array_equal(env.features, bundle.normalizer.transform(inputs["features"]))
    finally:
        env.close()


def test_real_ppo_train_save_load_and_observation_identity(tmp_path):
    cfg, inputs = load_app_config("config/app.yaml"), sample_inputs()
    cfg.training.device = "cpu"
    cfg.training.total_timesteps = 16
    cfg.training.n_envs = 1
    cfg.training.ppo.n_steps = 8
    cfg.training.ppo.batch_size = 8
    cfg.simulator.steps = 4
    cfg.env.breaker.stall_check_enabled = False
    cfg.training.checkpoint_dir = str(tmp_path / "models")
    cfg.training.tensorboard_dir = str(tmp_path / "events")
    cfg.training.log_dir = str(tmp_path / "logs")
    old_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        model = train(cfg, inputs=inputs, timeframe="5m")
        artifact = tmp_path / "models" / "final_model.zip"
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        bundle = read_model_bundle(artifact, expected_model_sha256=digest)
        inspected = inspect_model_bundle(artifact, expected_model_sha256=digest)
        assert inspected["qualification"]["training_market"] == "hashed"
        assert inspected["qualification"]["evaluation"] == "requires_separate_selection_and_final_evidence"
        json.dumps(inspected, allow_nan=False)
        loaded = PPO.load(artifact, device="cpu")
        assert bundle == bundle_for_model(model) == bundle_for_model(loaded)
        env = environment.create_environment(cfg, inputs, training_stats=bundle.training_stats)
        try:
            observation, _ = env.reset(seed=cfg.seed)
            np.testing.assert_array_equal(env.features, bundle.normalizer.transform(inputs["features"]))
            expected, _ = model.predict(observation, deterministic=True)
            actual, _ = loaded.predict(observation, deterministic=True)
            np.testing.assert_array_equal(actual, expected)
        finally:
            env.close()
    finally:
        torch.set_num_threads(old_threads)
