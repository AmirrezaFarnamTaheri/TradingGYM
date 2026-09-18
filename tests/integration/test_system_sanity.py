import pytest
import numpy as np
import gymnasium as gym
from trading_gym import make

# Skip if heavy dependencies aren't installed
try:
    from stable_baselines3 import PPO
except ImportError:
    pytest.skip("Stable-Baselines3/Torch not installed", allow_module_level=True)


def test_env_model_handshake(app_config, synthetic_data):
    """
    HARDENING CHECK: Verifies that the Environment's observation space
    perfectly matches the PPO Agent's input expectations.
    """
    # 1. Force a specific timeframe for deterministic testing
    timeframe = "5m"

    # 2. Initialize the real environment (using synthetic data fixture)
    # We mock the data loader for the test context
    import os
    import pandas as pd

    # Ensure lake dir is set to temp location
    app_config.data.lake_dir = "/tmp/lake"

    os.makedirs("/tmp/lake/BTCUSDT/5m", exist_ok=True)
    # Create sufficient data points to satisfy rolling window requirements (min 20)
    n_rows = 100
    # Generate data with tight spreads (2bps) to avoid max_valid_spread_bps (100bps) check
    # Mid: 100.0 -> High: 100.01, Low: 99.99 -> Spread: 0.02 / 100.0 = 0.0002 = 2bps
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
            "volume": np.ones(n_rows) * 1000,
        }
    )
    # The admitted-data loader requires a timezone-aware DatetimeIndex.
    _snap.index = pd.date_range("2024-01-01", periods=n_rows, freq="5min", tz="UTC")
    _snap.to_parquet("/tmp/lake/BTCUSDT/5m/snap.parquet")
    env = make(
        app_config, timeframe=timeframe, training_stats={"mean": 0.0, "std": 1.0}
    )

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

    print("\n✅ System Sanity Check Passed: Env <-> PPO compatibility verified.")
