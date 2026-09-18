from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from trading_gym.common.config import (
    AttentionConfig,
    FeatureConfig,
    StandardizationConfig,
    TimeframeConfig,
)
from trading_gym.pipelines.feature_pipeline import load_raw_data, run_feature_pipeline


def _raw(rows: int = 100) -> pd.DataFrame:
    close = np.linspace(100.0, 120.0, rows)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2024-01-01", periods=rows, freq="5min", tz="UTC"
            ),
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.linspace(10.0, 20.0, rows),
            "spread_bps": np.full(rows, 2.0),
        }
    )


def _config(tmp_path: Path):
    features = FeatureConfig(
        timeframes=[TimeframeConfig(name="5m", bar_minutes=5, roll_fill_cap=1)],
        standardization=StandardizationConfig(),
        attention=AttentionConfig(),
        rolling_window=100,
    )
    data = SimpleNamespace(
        raw_dir=str(tmp_path / "raw"),
        lake_dir=str(tmp_path / "lake"),
        features_dir=str(tmp_path / "features"),
        symbols=["BTC"],
        timeframes=["5m"],
    )
    Path(data.raw_dir).mkdir()
    Path(data.lake_dir).mkdir()
    return SimpleNamespace(data=data, features=features)


def _fake_to_parquet(self, path, **_kwargs):
    Path(path).write_bytes(b"PAR1" + self.to_csv(index=False).encode("utf-8"))


def test_legacy_csv_is_strictly_admitted(tmp_path):
    cfg = _config(tmp_path)
    source = Path(cfg.data.raw_dir) / "BTC_5m.csv"
    _raw().to_csv(source, index=False)

    admitted = load_raw_data("BTC", "5m", cfg.data.raw_dir, cfg.data.lake_dir)

    assert len(admitted) == 100
    assert admitted.index.is_monotonic_increasing
    assert admitted.index.is_unique
    assert admitted.attrs["source_kind"] == "legacy-file"
    assert len(admitted.attrs["source_sha256"]) == 64


def test_ambiguous_legacy_sources_are_rejected(tmp_path):
    cfg = _config(tmp_path)
    _raw().to_csv(Path(cfg.data.raw_dir) / "BTC_5m.csv", index=False)
    (Path(cfg.data.lake_dir) / "BTC_5m.csv").write_text(
        "timestamp,open\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Ambiguous"):
        load_raw_data("BTC", "5m", cfg.data.raw_dir, cfg.data.lake_dir)


def test_feature_pipeline_publishes_atomic_artifact_and_receipt(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    raw = _raw().set_index("timestamp", drop=False)
    raw.attrs["source_kind"] = "test"
    raw.attrs["source_sha256"] = "a" * 64
    monkeypatch.setattr(
        "trading_gym.pipelines.feature_pipeline.load_raw_data",
        lambda *_args, **_kwargs: raw.copy(),
    )
    monkeypatch.setattr(pd.DataFrame, "to_parquet", _fake_to_parquet)

    report = run_feature_pipeline(cfg)

    artifact = Path(cfg.data.features_dir) / "BTC_5m_features.parquet"
    receipt = artifact.with_suffix(".parquet.manifest.json")
    assert artifact.is_file()
    assert receipt.is_file()
    assert not artifact.with_suffix(".parquet.integrity-pending").exists()
    assert report["published"] == 1
    assert report["failed"] == 0


def test_feature_publication_failure_leaves_integrity_marker(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    raw = _raw().set_index("timestamp", drop=False)
    monkeypatch.setattr(
        "trading_gym.pipelines.feature_pipeline.load_raw_data",
        lambda *_args, **_kwargs: raw.copy(),
    )
    monkeypatch.setattr(pd.DataFrame, "to_parquet", _fake_to_parquet)
    original = __import__(
        "trading_gym.pipelines.feature_pipeline", fromlist=["atomic_write_json"]
    ).atomic_write_json

    def fail_receipt(path, value):
        if str(path).endswith(".manifest.json"):
            raise RuntimeError("receipt unavailable")
        return original(path, value)

    monkeypatch.setattr(
        "trading_gym.pipelines.feature_pipeline.atomic_write_json", fail_receipt
    )
    with pytest.raises(RuntimeError, match="Feature pipeline failed"):
        run_feature_pipeline(cfg)

    marker = Path(cfg.data.features_dir) / "BTC_5m_features.parquet.integrity-pending"
    assert marker.exists()


def test_feature_pipeline_rejects_nonfinite_assembler_output(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    raw = _raw().set_index("timestamp", drop=False)
    monkeypatch.setattr(
        "trading_gym.pipelines.feature_pipeline.load_raw_data",
        lambda *_args, **_kwargs: raw.copy(),
    )

    class BadAssembler:
        def __init__(self, _config):
            pass

        def process(self, frame, _symbol):
            return pd.DataFrame({"bad": [np.nan] * len(frame)}, index=frame.index)

    monkeypatch.setattr(
        "trading_gym.pipelines.feature_pipeline.FeatureAssembler", BadAssembler
    )
    with pytest.raises(RuntimeError, match="Feature pipeline failed"):
        run_feature_pipeline(cfg)
