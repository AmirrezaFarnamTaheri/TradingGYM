from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading_gym.common.config import load_app_config
from trading_gym.features.feature_assembler import FeatureAssembler

ROOT = Path(__file__).resolve().parents[2]


def fixture():
    cfg = load_app_config(ROOT / "config/app.yaml").features
    cfg.rolling_window = 20
    frame = pd.DataFrame({"close": np.full(30,100), "high": np.full(30,110),
                          "low": np.full(30,90), "spread_bps": np.full(30,20)},
                         index=pd.date_range("2026-01-01", periods=30, freq="5min", tz="UTC"))
    return cfg, frame


def test_spread_and_candle_range_have_distinct_units():
    cfg, frame = fixture()
    out = FeatureAssembler(cfg).process(frame,"BTCUSDT")
    assert out["spread_ratio"].iloc[-1] == pytest.approx(0.002)
    assert out["range_ratio"].iloc[-1] == pytest.approx(0.2)
    assert out.attrs["spread_source"] == "admitted"


def test_missing_quote_needs_declared_assumption_and_keeps_provenance():
    cfg, frame = fixture()
    frame = frame.drop(columns=["spread_bps"])
    cfg.assumed_spread_bps = None
    with pytest.raises(ValueError, match="explicitly configured"):
        FeatureAssembler(cfg).process(frame,"BTCUSDT")
    cfg.assumed_spread_bps = 30
    out = FeatureAssembler(cfg).process(frame,"BTCUSDT")
    assert out["spread_ratio"].iloc[-1] == pytest.approx(0.003)
    assert out.attrs["spread_source"] == "assumed"


@pytest.mark.parametrize("spread", [-1, float("nan"), float("inf"), 20000])
def test_invalid_admitted_spread_is_rejected(spread):
    cfg, frame = fixture()
    frame["spread_bps"] = spread
    with pytest.raises(ValueError, match="spread_bps"):
        FeatureAssembler(cfg).process(frame,"BTCUSDT")
