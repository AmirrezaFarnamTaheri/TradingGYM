import numpy as np
import pandas as pd
import pytest

from trading_gym.common.config import load_app_config
from trading_gym.pipelines.environment import (
    load_environment_inputs,
    create_environment,
    build_env_factory,
)
from trading_gym.rl.margin_trading_environment import MarginTradingEnv
from trading_gym.pipelines.availability import feature_history_rows
from trading_gym.pipelines.model_bundle import FrozenNormalizer


@pytest.fixture
def dummy_data_path(tmp_path):
    """Creates a dummy data lake structure for testing."""
    cfg = load_app_config("config/app.yaml")
    symbol = cfg.env.symbol
    timeframe = "5m"
    data_dir = tmp_path / symbol / timeframe
    data_dir.mkdir(parents=True)

    # Create dummy data with all required columns
    # Include the full causal feature history plus a nonempty replay interval.
    n_rows = 96
    np.random.seed(42)
    base_price = 100.0
    prices = base_price + np.cumsum(np.random.randn(n_rows) * 0.5)

    # OHLC built so the high/low envelope always contains open/close.
    opens = np.empty(n_rows)
    opens[0] = prices[0]
    opens[1:] = prices[:-1]
    closes = prices + np.random.randn(n_rows) * 0.1
    highs = np.maximum(opens, closes) + np.abs(np.random.randn(n_rows) * 0.3)
    lows = np.minimum(opens, closes) - np.abs(np.random.randn(n_rows) * 0.3)

    data = {
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "mid": prices,
        "volume": np.random.randint(1000, 2000, n_rows),
        "spread_bps": np.random.uniform(9.0, 11.0, n_rows),
        "buy_cap": np.full(n_rows, 10000),
        "sell_cap": np.full(n_rows, 10000),
    }
    df = pd.DataFrame(data)
    # The admitted-data loader requires a timezone-aware DatetimeIndex.
    df.index = pd.date_range("2024-01-01", periods=n_rows, freq="5min", tz="UTC")
    df.to_parquet(data_dir / "snap.parquet")
    return tmp_path


@pytest.mark.integration
def test_load_environment_inputs(dummy_data_path):
    """Tests that inputs are loaded correctly and have the right types."""
    cfg = load_app_config("config/app.yaml")
    cfg.data.lake_dir = str(dummy_data_path)
    cfg.features.attention.enabled = False

    inputs = load_environment_inputs(cfg, "5m")

    assert isinstance(inputs, dict)
    assert "features" in inputs and isinstance(inputs["features"], pd.DataFrame)
    assert "prices_mid" in inputs and isinstance(inputs["prices_mid"], np.ndarray)
    assert "prices_high" in inputs and isinstance(inputs["prices_high"], np.ndarray)
    assert "prices_low" in inputs and isinstance(inputs["prices_low"], np.ndarray)
    assert (
        len(inputs["prices_mid"]) == 96 - feature_history_rows(cfg.features) + 1
    )  # Updated for proper feature calculation


@pytest.mark.integration
def test_create_environment(dummy_data_path):
    """Tests that an environment can be created from loaded inputs."""
    cfg = load_app_config("config/app.yaml")
    cfg.data.lake_dir = str(dummy_data_path)
    cfg.features.attention.enabled = False

    inputs = load_environment_inputs(cfg, "5m")
    stats = FrozenNormalizer.fit(inputs["features"]).to_stats()
    env = create_environment(cfg, inputs, training_stats=stats)

    assert isinstance(env, MarginTradingEnv)
    assert (
        env.T == 96 - feature_history_rows(cfg.features) + 1
    )  # Updated for proper feature calculation
    # max_steps is min(1000, available_steps)


@pytest.mark.integration
def test_build_env_factory(dummy_data_path):
    """Tests that the environment factory can be built and can create an environment."""
    cfg = load_app_config("config/app.yaml")
    cfg.data.lake_dir = str(dummy_data_path)
    cfg.features.attention.enabled = False

    inputs = load_environment_inputs(cfg, "5m")
    stats = FrozenNormalizer.fit(inputs["features"]).to_stats()
    factory = build_env_factory(cfg, "5m", training_stats=stats)
    env = factory()

    assert isinstance(env, MarginTradingEnv)
    assert (
        env.T == 96 - feature_history_rows(cfg.features) + 1
    )  # Updated for proper feature calculation
