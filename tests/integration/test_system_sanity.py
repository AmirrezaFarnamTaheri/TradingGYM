import pytest
import numpy as np
import gymnasium as gym
from trading_gym import make

# Skip if heavy dependencies aren't installed
try:
    from stable_baselines3 import PPO
except ImportError:
    pytest.skip("Stable-Baselines3/Torch not installed", allow_module_level=True)


def test_env_model_handshake(app_config, tmp_path):
    """
    HARDENING CHECK: Verifies that the Environment's observation space
    perfectly matches the PPO Agent's input expectations.
    """
    # 1. Force a specific timeframe for deterministic testing
    timeframe = "5m"

    # 2. Build an isolated base-timeframe training tape and freeze its stats.
    import pandas as pd
    from trading_gym.rl.pipelines.environment import load_environment_inputs
    from trading_gym.rl.pipelines.model_bundle import FrozenNormalizer

    cfg = app_config.model_copy(deep=True)
    cfg.features.attention.enabled = False
    cfg.data.lake_dir = str(tmp_path / "lake")
    snapshot_path = tmp_path / "lake" / cfg.env.symbol / timeframe / "snap.parquet"
    snapshot_path.parent.mkdir(parents=True)

    # More than the admitted 64 completed history rows leaves a real transition.
    n_rows = 100
    # Declare the modeled spread independently of the candle's high/low range.
    mid_prices = np.linspace(100, 200, n_rows)
    _snap = pd.DataFrame(
        {
            "open": mid_prices,
            "close": mid_prices,
            "buy_cap": np.ones(n_rows),
            "sell_cap": np.ones(n_rows),
            "mid": mid_prices,
            "high": mid_prices + 0.01,
            "low": mid_prices - 0.01,
            "spread_bps": np.full(n_rows, 2.0),
            "volume": np.ones(n_rows) * 1000,
        }
    )
    # The admitted-data loader requires a timezone-aware DatetimeIndex.
    _snap.index = pd.date_range("2024-01-01", periods=n_rows, freq="5min", tz="UTC")
    _snap.to_parquet(snapshot_path)
    training_inputs = load_environment_inputs(cfg, timeframe)
    stats = FrozenNormalizer.fit(
        training_inputs["features"], cfg.features.standardization.method
    ).to_stats()
    env = make(cfg, timeframe=timeframe, training_stats=stats)

    assert isinstance(env.observation_space, gym.spaces.Box), "Obs space must be a Box"
    assert isinstance(env.action_space, gym.spaces.Box), "Action space must be a Box"

    # 3. Initialize PPO Agent (CPU mode for CI/Test)
    model = PPO(
        "MlpPolicy",
        env,
        verbose=1,
        device="cpu",
        # Minimal config for speed
        n_steps=128,
        batch_size=64,
    )

    # 4. Execution Loop Test
    obs, _ = env.reset()

    # Ensure observation is not NaN/Inf immediately after reset
    assert np.all(np.isfinite(obs)), "Initial observation contains NaNs or Infs!"
    assert obs.shape == env.observation_space.shape, "Observation shape mismatch!"

    # Predict action
    action, _ = model.predict(obs, deterministic=True)

    # Step environment
    new_obs, reward, terminated, truncated, info = env.step(action)

    # 5. Post-Step Verification
    assert np.all(np.isfinite(new_obs)), "Post-step observation contains NaNs!"
    assert isinstance(reward, float), "Reward must be float"
    assert "equity" in info, "Info dict missing 'equity'"
    env.close()

    print("\n✅ System Sanity Check Passed: Env <-> PPO compatibility verified.")
