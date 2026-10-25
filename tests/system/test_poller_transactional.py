from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from trading_gym.data.lake_cli.manifest import verify_lake
from trading_gym.data.poller.main import Poller
from trading_gym.data.data_loader import load_data_from_lake


def _batch() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2024-01-01T00:00:00Z", "2024-01-01T00:05:00Z"], utc=True
            ),
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [10.0, 11.0],
            "spread_bps": [2.0, 2.0],
        }
    )


def _fake_parquet_write(self, path, index=False):
    Path(path).write_bytes(b"PAR1" + self.to_csv(index=index).encode("utf-8"))


def test_fetch_batch_rejects_out_of_range_and_malformed_candles(tmp_path):
    client = MagicMock()
    poller = Poller("BTC", "5m", client, tmp_path / "lake", tmp_path / "manifests")
    client.get_klines.return_value = [[1_700_000_000, 100, 101, 99, 100, 10, "extra"]]
    with pytest.raises(ValueError, match="exactly six"):
        poller.fetch_batch(1_700_000_000, 1_700_000_300)

    client.get_klines.return_value = [[1_700_001_000, 100, 101, 99, 100, 10]]
    with pytest.raises(ValueError, match="outside"):
        poller.fetch_batch(1_700_000_000, 1_700_000_300)


def test_save_batch_publishes_nested_manifest_and_clears_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(pd.DataFrame, "to_parquet", _fake_parquet_write)
    lake = tmp_path / "lake"
    manifests = tmp_path / "manifests"
    poller = Poller("BTC", "5m", MagicMock(), lake, manifests)

    paths = poller.save_batch(_batch())

    assert len(paths) == 1
    assert paths[0].name == "part_2024-01-01.parquet"
    assert not (lake / "BTC" / "5m" / ".integrity-pending").exists()
    assert verify_lake(lake, manifests, manifests / "daily.merkle.json") is True


def test_manifest_failure_leaves_fail_closed_partition_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(pd.DataFrame, "to_parquet", _fake_parquet_write)
    monkeypatch.setattr(
        "trading_gym.data.poller.main.write_daily_merkle",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    lake = tmp_path / "lake"
    manifests = tmp_path / "manifests"
    poller = Poller("BTC", "5m", MagicMock(), lake, manifests)

    with pytest.raises(RuntimeError):
        poller.save_batch(_batch())

    marker = lake / "BTC" / "5m" / ".integrity-pending"
    assert marker.exists()
    monkeypatch.setattr(pd, "read_parquet", lambda _path: _batch())
    with pytest.raises(ValueError, match="integrity update is incomplete"):
        load_data_from_lake(lake, ("BTC",), ("5m",))


def test_poller_rejects_unsafe_identifiers_and_unbounded_ranges(tmp_path):
    with pytest.raises(ValueError, match="Unsafe symbol"):
        Poller("../BTC", "5m", MagicMock(), tmp_path / "lake")
    poller = Poller("BTC", "5m", MagicMock(), tmp_path / "lake")
    with pytest.raises(ValueError, match="candle limit"):
        poller.fetch_batch(1_700_000_000, 1_700_000_000 + 5_001 * 300)
