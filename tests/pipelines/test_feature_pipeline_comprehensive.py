import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock, patch
from trading_gym.pipelines.feature_pipeline import (
    load_raw_data,
    run_feature_pipeline,
)


class SimpleConfig:
    def __init__(self, raw, lake, feat):
        self.raw_dir = raw
        self.lake_dir = lake
        self.features_dir = feat
        self.symbols = ["BTC"]
        self.timeframes = ["5m"]


class AppConfigMock:
    def __init__(self, data_cfg, feat_cfg):
        self.data = data_cfg
        self.features = feat_cfg


@pytest.fixture
def mock_config(tmp_path):
    data_cfg = SimpleConfig(
        str(tmp_path / "raw"), str(tmp_path / "lake"), str(tmp_path / "features")
    )
    feat_cfg = MagicMock()
    feat_cfg.timeframes = ["5m"]
    feat_cfg.model_dump.return_value = {"timeframes": ["5m"]}

    # Create dirs
    Path(data_cfg.raw_dir).mkdir(parents=True, exist_ok=True)
    Path(data_cfg.lake_dir).mkdir(parents=True, exist_ok=True)
    Path(data_cfg.features_dir).mkdir(parents=True, exist_ok=True)

    return AppConfigMock(data_cfg, feat_cfg)


@pytest.fixture
def sample_ohlcv():
    dates = pd.date_range(start="2024-01-01", periods=100, freq="5min")
    rng = np.random.default_rng(42)
    open_price = 100 + rng.normal(0, 0.5, 100).cumsum()
    close = open_price + rng.normal(0, 0.25, 100)
    high = np.maximum(open_price, close) + rng.uniform(0.01, 0.5, 100)
    low = np.minimum(open_price, close) - rng.uniform(0.01, 0.5, 100)
    df = pd.DataFrame(
        {
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(1, 1000, 100),
        },
        index=dates,
    )
    return df


class TestLoadRawData:
    def test_load_from_lake_parquet(self, mock_config, sample_ohlcv):
        pytest.importorskip("pyarrow")
        symbol = "BTC"
        tf = "5m"
        # Match pattern: lake_dir / f"{symbol}_{timeframe}.parquet"
        file_path = Path(mock_config.data.lake_dir) / f"{symbol}_{tf}.parquet"
        sample_ohlcv.to_parquet(file_path)

        df = load_raw_data(
            symbol, tf, Path(mock_config.data.raw_dir), Path(mock_config.data.lake_dir)
        )

        assert len(df) == 100
        assert isinstance(df.index, pd.DatetimeIndex)

    def test_load_from_raw_csv(self, mock_config, sample_ohlcv):
        symbol = "BTC"
        tf = "5m"
        # Match pattern: raw_dir / f"{symbol}_{timeframe}.csv"
        raw_path = Path(mock_config.data.raw_dir) / f"{symbol}_{tf}.csv"
        # Reset index to simulate CSV with timestamp column
        csv_df = sample_ohlcv.reset_index().rename(columns={"index": "datetime"})
        csv_df.to_csv(raw_path, index=False)

        df = load_raw_data(
            symbol, tf, Path(mock_config.data.raw_dir), Path(mock_config.data.lake_dir)
        )

        assert len(df) == 100

    def test_missing_file_raises_error(self, mock_config):
        with pytest.raises(FileNotFoundError):
            load_raw_data(
                "INVALID",
                "5m",
                Path(mock_config.data.raw_dir),
                Path(mock_config.data.lake_dir),
            )


class TestRunFeaturePipeline:
    @patch("trading_gym.pipelines.feature_pipeline.FeatureAssembler")
    @patch("trading_gym.pipelines.feature_pipeline.load_raw_data")
    @patch("trading_gym.pipelines.feature_pipeline._publish_features")
    def test_successful_run(self, mock_publish, mock_load, MockAssembler, mock_config, sample_ohlcv):
        mock_load.return_value = sample_ohlcv
        assembler_instance = MockAssembler.return_value
        assembler_instance.process.return_value = sample_ohlcv
        mock_publish.return_value = {"sha256": "a" * 64}

        report = run_feature_pipeline(mock_config)

        assert report["published"] == 1
        assert report["failed"] == 0
        mock_publish.assert_called_once()
