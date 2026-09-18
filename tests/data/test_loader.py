import pytest
import pandas as pd
from trading_gym.data.data_loader import load_data_from_lake, load_symbol_timeframe

pytest.importorskip(
    "pyarrow", reason="Parquet loader tests require the declared pyarrow dependency"
)


@pytest.fixture
def mock_lake(tmp_path):
    symbol_dir = tmp_path / "BTC" / "5m"
    symbol_dir.mkdir(parents=True)

    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 00:05"]),
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [1000.0, 2000.0],
        }
    )

    df.to_parquet(symbol_dir / "part.parquet")
    return tmp_path


def test_load_symbol_timeframe_success(mock_lake):
    # Correct usage: Path object, symbol, timeframe
    df = load_symbol_timeframe(mock_lake, "BTC", "5m")
    assert len(df) == 2
    assert "open" in df.columns


def test_load_missing_symbol(mock_lake):
    with pytest.raises(FileNotFoundError):
        load_symbol_timeframe(mock_lake, "ETH", "5m")


def test_load_data_from_lake_integration(mock_lake):
    # Correct usage: Path object, tuples
    data = load_data_from_lake(mock_lake, ("BTC",), ("5m",))

    assert "BTC" in data
    assert "5m" in data["BTC"]
    assert len(data["BTC"]["5m"]) == 2
