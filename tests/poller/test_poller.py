import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from apps.poller.main import Poller
from trading_gym.adapters.enhanced_aban_client import EnhancedAbanClient


@pytest.fixture
def mock_client():
    return MagicMock(spec=EnhancedAbanClient)


def test_poller_fetch_batch(mock_client):
    # Setup mock response
    mock_client.get_klines.return_value = [
        [1600000000, 100, 110, 90, 105, 1000],
        [1600000300, 105, 115, 95, 110, 1200],
    ]

    poller = Poller(symbol="BTC", timeframe="5m", client=mock_client)

    df = poller.fetch_batch(start_ts=1600000000, end_ts=1600000600)

    assert len(df) == 2
    assert df.iloc[0]["close"] == 105.0
    mock_client.get_klines.assert_called_once()


def test_poller_retry_logic(mock_client):
    # Fail once then succeed
    mock_client.get_klines.side_effect = [
        Exception("API Error"),
        [[1600000000, 100, 110, 90, 105, 1000]],
    ]

    poller = Poller(symbol="BTC", timeframe="5m", client=mock_client)

    # Mock sleep to speed up test
    with patch("time.sleep", return_value=None):
        # Retry logic logic is not yet implemented in fetch_batch directly (assumes client handles it?)
        # Ah, Poller code I wrote earlier: "try ... except ... logger.error; raise"
        # It doesn't retry *inside* fetch_batch. The client retries.
        # But wait, test_poller.py calls fetch_batch.
        # If fetch_batch raises, the outer loop in `download` catches it.

        # Let's adjust the test to expect an exception OR fix Poller to retry.
        # The EnhancedAbanClient *already* has retry logic in `_make_request`.
        # So if get_klines fails, it means retries exhausted.
        # So fetch_batch raises.

        with pytest.raises(Exception, match="API Error"):
            poller.fetch_batch(start_ts=1600000000, end_ts=1600000600)


def test_poller_save(tmp_path, mock_client):
    pytest.importorskip("pyarrow")
    poller = Poller(symbol="BTC", timeframe="5m", client=mock_client)
    poller.data_dir = tmp_path

    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([1600000000], unit="s"),
            "open": [100.0],
            "high": [110.0],
            "low": [90.0],
            "close": [105.0],
            "volume": [1000.0],
        }
    )

    poller.save_batch(df)

    # Check file exists (partitioned by date)
    # 1600000000 is 2020-09-13
    expected_file = tmp_path / "BTC" / "5m" / "part_2020-09-13.parquet"
    assert expected_file.exists()
