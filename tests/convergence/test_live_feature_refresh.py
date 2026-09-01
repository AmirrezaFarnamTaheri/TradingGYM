from __future__ import annotations

from copy import deepcopy
from typing import Any

import numpy as np
import pytest

from apps.common.live_features import build_live_feature_snapshot
from trading_gym.common.config import load_app_config

NOW = 2_000_000_000


def _rows(bar_seconds: int, *, count: int = 40, forming_close: float = 999_999.0) -> list[list[float]]:
    start = NOW - bar_seconds * (count + 1)
    rows: list[list[float]] = []
    for index in range(count):
        timestamp = start + index * bar_seconds
        close = 100.0 + index
        rows.append([timestamp, close - 0.5, close + 1, close - 1, close, 10 + index])
    # The current forming candle must be excluded from feature generation.
    forming_timestamp = NOW - bar_seconds
    rows.append(
        [
            forming_timestamp,
            forming_close - 1,
            forming_close + 2,
            forming_close - 2,
            forming_close,
            50,
        ]
    )
    return rows


class Client:
    def __init__(self) -> None:
        self.payloads: dict[str, list[Any]] = {
            "5m": _rows(300),
            "1h": _rows(3600),
            "4h": _rows(14_400),
        }
        self.calls: list[tuple[str, str, int, int, int]] = []

    def get_klines(self, symbol, interval, start=None, end=None, limit=1000):  # type: ignore[no-untyped-def]
        self.calls.append((symbol, interval, start, end, limit))
        return deepcopy(self.payloads[interval])


def _config():
    cfg = load_app_config("config/app.yaml")
    # Keep the real configured three-timeframe attention path, but use a bounded
    # feature window suitable for a focused deterministic test.
    return cfg.model_copy(
        update={
            "features": cfg.features.model_copy(update={"rolling_window": 100})
        }
    )


def test_live_snapshot_uses_closed_candles_and_all_configured_timeframes() -> None:
    client = Client()
    snapshot = build_live_feature_snapshot(client, _config(), now=NOW)
    assert len(client.calls) == 3
    assert {call[1] for call in client.calls} == {"5m", "1h", "4h"}
    assert snapshot.base_close != 999_999.0
    assert snapshot.base_candle_timestamp == NOW - 600
    assert snapshot.feature_row.flags.writeable is False
    assert snapshot.feature_row.shape == (len(snapshot.feature_columns),)
    assert np.isfinite(snapshot.feature_row).all()
    assert len(snapshot.source_sha256) == 64


def test_live_snapshot_is_deterministic_for_identical_source_data() -> None:
    client = Client()
    first = build_live_feature_snapshot(client, _config(), now=NOW)
    second = build_live_feature_snapshot(client, _config(), now=NOW)
    assert first.source_sha256 == second.source_sha256
    assert np.array_equal(first.feature_row, second.feature_row)


def test_live_snapshot_rejects_invalid_ohlc_envelope() -> None:
    client = Client()
    client.payloads["5m"][-2][2] = 1.0  # high below open/close
    with pytest.raises(ValueError, match="OHLC envelope"):
        build_live_feature_snapshot(client, _config(), now=NOW)


def test_live_snapshot_rejects_duplicates_and_stale_history() -> None:
    duplicate = Client()
    duplicate.payloads["1h"][-2][0] = duplicate.payloads["1h"][-3][0]
    with pytest.raises(ValueError, match="unique"):
        build_live_feature_snapshot(duplicate, _config(), now=NOW)

    stale = Client()
    stale.payloads["4h"] = [
        [row[0] - 200_000, *row[1:]] for row in stale.payloads["4h"]
    ]
    with pytest.raises(ValueError, match="stale"):
        build_live_feature_snapshot(stale, _config(), now=NOW)


def test_live_snapshot_rejects_excessive_history_and_rolling_window() -> None:
    client = Client()
    client.payloads["5m"] = _rows(300, count=1001)
    with pytest.raises(ValueError, match="exceeds"):
        build_live_feature_snapshot(client, _config(), now=NOW)

    cfg = _config()
    oversized = cfg.model_copy(
        update={
            "features": cfg.features.model_copy(update={"rolling_window": 20_000})
        }
    )
    with pytest.raises(ValueError, match="rolling window"):
        build_live_feature_snapshot(Client(), oversized, now=NOW)
