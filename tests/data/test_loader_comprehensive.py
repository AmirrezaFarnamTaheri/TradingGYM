import pytest
import pandas as pd
from trading_gym.data.data_loader import (
    load_data_from_lake,
    load_symbol_timeframe,
    get_available_partitions,
)


class TestDataLoaderComprehensive:
    @pytest.fixture
    def lake_dir(self, tmp_path):
        return tmp_path / "lake"

    def test_load_symbol_timeframe_parquet(self, lake_dir):
        pytest.importorskip("pyarrow")
        # Setup lake structure
        symbol_dir = lake_dir / "BTC" / "5m"
        symbol_dir.mkdir(parents=True)

        df = pd.DataFrame(
            {
                "open": [100.0, 101.0],
                "high": [102.0, 103.0],
                "low": [99.0, 100.0],
                "close": [101.0, 102.0],
                "volume": [10.0, 15.0],
                "spread_bps": [1.0, 1.0],
                "timestamp": pd.date_range("2024-01-01", periods=2, freq="5min"),
            }
        )

        # Save dummy parquet
        df.to_parquet(symbol_dir / "data.parquet")

        loaded_df = load_symbol_timeframe(lake_dir, "BTC", "5m")

        assert len(loaded_df) == 2
        assert "open" in loaded_df.columns
        # Check index
        assert isinstance(loaded_df.index, pd.DatetimeIndex)

    def test_load_data_from_lake_multiple(self, lake_dir):
        pytest.importorskip("pyarrow")
        # BTC Setup
        btc_dir = lake_dir / "BTC" / "5m"
        btc_dir.mkdir(parents=True)
        df_btc = pd.DataFrame(
            {
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.0],
                "volume": [10.0],
                "spread_bps": [1.0],
                "timestamp": pd.to_datetime(["2024-01-01 00:00:00"]),
            }
        )
        df_btc.to_parquet(btc_dir / "part1.parquet")

        # ETH Setup
        eth_dir = lake_dir / "ETH" / "5m"
        eth_dir.mkdir(parents=True)
        df_eth = pd.DataFrame(
            {
                "open": [200.0],
                "high": [201.0],
                "low": [199.0],
                "close": [200.0],
                "volume": [20.0],
                "spread_bps": [2.0],
                "timestamp": pd.to_datetime(["2024-01-01 00:00:00"]),
            }
        )
        df_eth.to_parquet(eth_dir / "part1.parquet")

        data = load_data_from_lake(lake_dir, ("BTC", "ETH"), ("5m",))

        assert "BTC" in data
        assert "ETH" in data
        assert "5m" in data["BTC"]
        assert len(data["BTC"]["5m"]) == 1

    def test_missing_directory(self, lake_dir):
        with pytest.raises(FileNotFoundError):
            load_data_from_lake(lake_dir / "nonexistent", ("BTC",), ("5m",))

    def test_missing_partition(self, lake_dir):
        # Create lake but not partition
        lake_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            load_data_from_lake(lake_dir, ("BTC",), ("5m",))

    def test_schema_validation(self, lake_dir):
        pytest.importorskip("pyarrow")
        symbol_dir = lake_dir / "BTC" / "5m"
        symbol_dir.mkdir(parents=True)

        # Missing 'close'
        df = pd.DataFrame(
            {
                "open": [100.0],
                "high": [100.0],
                "low": [100.0],
                "volume": [10.0],
                "spread_bps": [1.0],
            }
        )
        df.index = pd.date_range("2024-01-01", periods=1, freq="5min", tz="UTC")
        df.to_parquet(symbol_dir / "bad.parquet")

        with pytest.raises(ValueError, match="missing critical columns"):
            load_data_from_lake(lake_dir, ("BTC",), ("5m",))

    def test_spread_backfill(self, lake_dir):
        pytest.importorskip("pyarrow")
        symbol_dir = lake_dir / "BTC" / "5m"
        symbol_dir.mkdir(parents=True)

        # Missing 'spread_bps' but has OHLCV
        df = pd.DataFrame(
            {
                "open": [100.0],
                "high": [100.0],
                "low": [100.0],
                "close": [100.0],
                "volume": [10.0],
                "timestamp": pd.to_datetime(["2024-01-01"]),
            }
        )
        df.to_parquet(symbol_dir / "nospread.parquet")

        data = load_data_from_lake(lake_dir, ("BTC",), ("5m",))
        loaded = data["BTC"]["5m"]
        assert "spread_bps" in loaded.columns
        assert loaded["spread_bps"].iloc[0] == 20.0

    def test_get_available_partitions(self, lake_dir):
        (lake_dir / "BTC" / "5m").mkdir(parents=True)
        (lake_dir / "BTC" / "5m" / "data.parquet").touch()
        (lake_dir / "BTC" / "1h").mkdir(parents=True)
        (lake_dir / "BTC" / "1h" / "data.parquet").touch()

        (lake_dir / "ETH" / "5m").mkdir(parents=True)
        (lake_dir / "ETH" / "5m" / "data.parquet").touch()

        parts = get_available_partitions(lake_dir)

        assert "BTC" in parts
        assert "1h" in parts["BTC"]
        assert "5m" in parts["BTC"]
        assert "ETH" in parts
