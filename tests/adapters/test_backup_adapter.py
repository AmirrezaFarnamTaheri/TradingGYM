from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pandas as pd
import pytest

pytest.importorskip(
    "pyarrow", reason="Parquet adapter tests require the declared pyarrow dependency"
)

from trading_gym.adapters.backup import LocalCacheAdapter  # noqa: E402


def _write_snapshot(path: Path, rows: List[dict]) -> Path:
    frame = pd.DataFrame(rows)
    frame.to_parquet(path, index=False)
    return path


def test_local_cache_adapter_reads_latest(tmp_path: Path) -> None:
    adapter = LocalCacheAdapter(cache_dir=tmp_path)
    timestamp = datetime.now(timezone.utc)

    _write_snapshot(
        tmp_path / "20240101_000000_000000.parquet",
        [
            {
                "symbol": "BTCUSDT",
                "buy_price": 100.0,
                "sell_price": 99.0,
                "fetch_timestamp_utc": timestamp,
            },
        ],
    )

    records = adapter.fetch_tickers()
    assert len(records) == 1
    record = records[0]
    assert record["symbol"] == "BTCUSDT"
    assert record["buy_price"] == 100.0
    assert record["sell_price"] == 99.0


def test_local_cache_adapter_filters_symbols(tmp_path: Path) -> None:
    adapter = LocalCacheAdapter(cache_dir=tmp_path)
    timestamp = datetime.now(timezone.utc)
    _write_snapshot(
        tmp_path / "20240101_010000_000000.parquet",
        [
            {
                "symbol": "BTCUSDT",
                "buy_price": 100.0,
                "sell_price": 99.0,
                "fetch_timestamp_utc": timestamp,
            },
            {
                "symbol": "ETHUSDT",
                "buy_price": 50.0,
                "sell_price": 49.5,
                "fetch_timestamp_utc": timestamp,
            },
        ],
    )

    records = adapter.fetch_tickers(symbols=["ethusdt"])
    assert len(records) == 1
    assert records[0]["symbol"] == "ETHUSDT"


def test_local_cache_adapter_respects_staleness(tmp_path: Path) -> None:
    adapter = LocalCacheAdapter(cache_dir=tmp_path, max_age=timedelta(minutes=1))
    stale_ts = datetime.now(timezone.utc) - timedelta(hours=1)
    _write_snapshot(
        tmp_path / "20240101_020000_000000.parquet",
        [
            {
                "symbol": "BTCUSDT",
                "buy_price": 100.0,
                "sell_price": 99.0,
                "fetch_timestamp_utc": stale_ts,
            },
        ],
    )

    assert adapter.fetch_tickers() == []
    health = adapter.healthcheck()
    assert health["ok"] is False
