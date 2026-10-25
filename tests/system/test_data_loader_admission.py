from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading_gym.data.data_loader import load_data_from_lake


def _frame(
    timestamp: str, *, close: float = 101.0, spread: bool = True
) -> pd.DataFrame:
    data = {
        "timestamp": pd.to_datetime([timestamp]),
        "open": [100.0],
        "high": [max(102.0, close)],
        "low": [min(99.0, close)],
        "close": [close],
        "volume": [10.0],
    }
    if spread:
        data["spread_bps"] = [2.0]
    return pd.DataFrame(data)


def _partition(tmp_path: Path, *names: str) -> Path:
    partition = tmp_path / "BTC" / "5m"
    partition.mkdir(parents=True)
    for name in names:
        (partition / name).write_bytes(name.encode())
    return partition


def test_loader_admits_complete_partition_and_records_provenance(tmp_path, monkeypatch):
    _partition(tmp_path, "a.parquet", "b.parquet")
    frames = {
        "a.parquet": _frame("2024-01-01T00:00:00Z", spread=False),
        "b.parquet": _frame("2024-01-01T00:05:00Z", close=102.0, spread=False),
    }
    monkeypatch.setattr(pd, "read_parquet", lambda path: frames[Path(path).name].copy())

    loaded = load_data_from_lake(tmp_path, ("BTC",), ("5m",))["BTC"]["5m"]

    assert list(loaded["close"]) == [101.0, 102.0]
    assert "timestamp" in loaded.columns
    assert str(loaded.index.tz) == "UTC"
    assert loaded.attrs["synthetic_spread_bps"] is True
    assert loaded.attrs["default_spread_bps"] == 20.0
    assert set(loaded.attrs["source_sha256"]) == {"a.parquet", "b.parquet"}


def test_loader_rejects_partial_partition_when_any_shard_fails(tmp_path, monkeypatch):
    _partition(tmp_path, "a.parquet", "b.parquet")

    def read(path):
        if Path(path).name == "b.parquet":
            raise RuntimeError("credential=secret")
        return _frame("2024-01-01T00:00:00Z")

    monkeypatch.setattr(pd, "read_parquet", read)
    with pytest.raises(ValueError, match="complete partition"):
        load_data_from_lake(tmp_path, ("BTC",), ("5m",))


def test_loader_rejects_conflicting_duplicate_candles(tmp_path, monkeypatch):
    _partition(tmp_path, "a.parquet", "b.parquet")
    frames = {
        "a.parquet": _frame("2024-01-01T00:00:00Z", close=101.0),
        "b.parquet": _frame("2024-01-01T00:00:00Z", close=102.0),
    }
    monkeypatch.setattr(pd, "read_parquet", lambda path: frames[Path(path).name].copy())
    with pytest.raises(ValueError, match="Conflicting duplicate"):
        load_data_from_lake(tmp_path, ("BTC",), ("5m",))


def test_loader_deduplicates_identical_candles_with_receipt(tmp_path, monkeypatch):
    _partition(tmp_path, "a.parquet", "b.parquet")
    monkeypatch.setattr(
        pd, "read_parquet", lambda _path: _frame("2024-01-01T00:00:00Z")
    )

    loaded = load_data_from_lake(tmp_path, ("BTC",), ("5m",))["BTC"]["5m"]

    assert len(loaded) == 1
    assert loaded.attrs["deduplicated_rows"] == 2


@pytest.mark.parametrize(
    "mutator,match",
    [
        (lambda frame: frame.assign(high=[98.0]), "OHLC envelope"),
        (lambda frame: frame.assign(close=[np.inf]), "Non-finite"),
        (lambda frame: frame.assign(volume=[-1.0]), "non-negative"),
    ],
)
def test_loader_rejects_invalid_market_values(tmp_path, monkeypatch, mutator, match):
    _partition(tmp_path, "a.parquet")
    monkeypatch.setattr(pd, "read_parquet", lambda _path: mutator(_frame("2024-01-01")))
    with pytest.raises(ValueError, match=match):
        load_data_from_lake(tmp_path, ("BTC",), ("5m",))


def test_loader_rejects_path_traversal_and_symlinked_members(tmp_path, monkeypatch):
    _partition(tmp_path, "a.parquet")
    monkeypatch.setattr(pd, "read_parquet", lambda _path: _frame("2024-01-01"))
    with pytest.raises(ValueError, match="Unsafe symbol"):
        load_data_from_lake(tmp_path, ("../BTC",), ("5m",))

    target = tmp_path / "outside.parquet"
    target.write_bytes(b"outside")
    link = tmp_path / "BTC" / "5m" / "linked.parquet"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="Unsafe parquet member"):
        load_data_from_lake(tmp_path, ("BTC",), ("5m",))


def test_loader_enforces_file_and_row_bounds(tmp_path, monkeypatch):
    _partition(tmp_path, "a.parquet", "b.parquet")
    monkeypatch.setattr(pd, "read_parquet", lambda _path: _frame("2024-01-01"))
    with pytest.raises(ValueError, match="File-count limit"):
        load_data_from_lake(
            tmp_path,
            ("BTC",),
            ("5m",),
            max_files_per_partition=1,
        )
    with pytest.raises(ValueError, match="Row limit"):
        load_data_from_lake(
            tmp_path,
            ("BTC",),
            ("5m",),
            max_rows_per_partition=1,
        )
