import pytest
import pandas as pd
import numpy as np
from trading_gym.data.features.feature_assembler import FeatureAssembler
from trading_gym.core.config import (
    FeatureConfig,
    TimeframeConfig,
    StandardizationConfig,
    AttentionConfig,
)


@pytest.fixture
def feature_config():
    return FeatureConfig(
        timeframes=[TimeframeConfig(name="5m", bar_minutes=5, roll_fill_cap=1)],
        standardization=StandardizationConfig(),
        attention=AttentionConfig(),
        rolling_window=100,
    )


@pytest.fixture
def sample_df():
    dates = pd.date_range("2023-01-01", periods=100, freq="5min")
    df = pd.DataFrame(
        {
            "open": np.linspace(100, 200, 100),
            "high": np.linspace(101, 201, 100),
            "low": np.linspace(99, 199, 100),
            "close": np.linspace(100, 200, 100),
            "spread_bps": np.full(100, 2.0),
            "volume": np.random.rand(100) * 1000,
        },
        index=dates,
    )
    return df


def test_process_shapes(feature_config, sample_df):
    assembler = FeatureAssembler(feature_config)
    features = assembler.process(sample_df, "BTCUSDT")

    assert len(features) == 100
    assert "log_ret" in features.columns
    assert "volatility" in features.columns
    assert "rsi" in features.columns
    assert "sector" in features.columns
    assert "can_short" in features.columns


def test_process_validates_input(feature_config):
    assembler = FeatureAssembler(feature_config)
    bad_data = pd.DataFrame({"col1": [1, 2, 3]})
    with pytest.raises(ValueError):
        assembler.process(bad_data, "BTCUSDT")


def test_sector_encoding(feature_config, sample_df):
    assembler = FeatureAssembler(feature_config)

    # BTC -> L1 -> 1
    f_btc = assembler.process(sample_df, "BTCUSDT")
    assert f_btc["sector"].iloc[0] == 1.0

    # DOGE -> MEME -> 2
    f_doge = assembler.process(sample_df, "DOGEUSDT")
    assert f_doge["sector"].iloc[0] == 2.0

    # UNKNOWN -> 0
    f_unk = assembler.process(sample_df, "UNKNOWNUSDT")
    assert f_unk["sector"].iloc[0] == 0.0


def test_short_allowance(feature_config, sample_df):
    assembler = FeatureAssembler(feature_config)

    # BTC -> Shortable
    f_btc = assembler.process(sample_df, "BTCUSDT")
    assert f_btc["can_short"].iloc[0] == 1.0

    # DOGE -> Not Shortable (based on registry defaults)
    f_doge = assembler.process(sample_df, "DOGEUSDT")
    assert f_doge["can_short"].iloc[0] == 0.0


def test_nan_input_is_rejected(feature_config, sample_df):
    sample_df.loc[sample_df.index[50], "close"] = np.nan
    assembler = FeatureAssembler(feature_config)
    with pytest.raises(ValueError, match="Non-finite"):
        assembler.process(sample_df, "BTCUSDT")
