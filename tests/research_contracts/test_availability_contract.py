"""Real feature kernels: available-at joins and replay/live observation parity."""

import numpy as np
import pandas as pd
import pytest

from apps.common.live_features import build_live_feature_snapshot
from trading_gym.common.config import load_app_config
from trading_gym.features.feature_assembler import FeatureAssembler
from trading_gym.pipelines import environment
from trading_gym.pipelines.availability import (
    SETTLEMENT_DELAY_SECONDS,
    align_available_frame,
    available_index,
    closed_feature_frame,
    feature_history_rows,
    active_timeframes,
)


def raw(n, minutes, start="2025-01-01"):
    index = pd.date_range(start, periods=n, freq=f"{minutes}min", tz="UTC")
    close = 100.0 + np.arange(n) * 0.01 + np.sin(np.arange(n) / 7.0)
    return pd.DataFrame(
        {
            "open": close,
            "close": close,
            "high": close + 0.01,
            "low": close - 0.01,
            "volume": np.ones(n),
            "mid": close,
        },
        index=index,
    )


def test_available_at_join_never_uses_a_not_yet_closed_bar():
    opens = pd.date_range("2025-01-01", periods=3, freq="1h", tz="UTC")
    features = pd.DataFrame(
        {"x": [10.0, 20.0, 30.0]}, index=available_index(opens, 3600)
    )
    decisions = pd.DatetimeIndex(
        [
            opens[1] + pd.Timedelta(seconds=2),
            opens[1] + pd.Timedelta(minutes=55, seconds=2),
            opens[2] + pd.Timedelta(seconds=2),
        ]
    )
    aligned = align_available_frame(features, decisions, max_age_seconds=3600)
    assert aligned.x.tolist() == [10.0, 10.0, 20.0]
    with pytest.raises(ValueError, match="not yet available"):
        align_available_frame(features, opens[:1], max_age_seconds=3600)
    with pytest.raises(ValueError, match="freshness"):
        align_available_frame(
            features.iloc[:1], features.index[1:2], max_age_seconds=3600
        )


def test_bounded_recipe_has_exact_live_tail_parity_and_future_invariance():
    cfg = load_app_config("config/app.yaml")
    assembler = FeatureAssembler(cfg.features)
    source = raw(170, 5)
    full = closed_feature_frame(source, assembler, cfg.env.symbol, bar_seconds=300)
    history = feature_history_rows(cfg.features)
    tail = closed_feature_frame(
        source.iloc[-history:], assembler, cfg.env.symbol, bar_seconds=300
    )
    np.testing.assert_array_equal(full.iloc[-1], tail.iloc[-1])
    changed = source.copy()
    changed.iloc[
        -10:, changed.columns.get_indexer(["open", "high", "low", "close", "mid"])
    ] *= 2
    mutated = closed_feature_frame(changed, assembler, cfg.env.symbol, bar_seconds=300)
    np.testing.assert_array_equal(full.iloc[:-10], mutated.iloc[:-10])


def test_offline_and_live_two_timeframes_emit_identical_closed_snapshot(monkeypatch):
    cfg = load_app_config("config/app.yaml")
    cfg.features.timeframes = cfg.features.timeframes[:2]
    frames = {"5m": raw(1200, 5), "1h": raw(103, 60)}
    base_close = frames["5m"].index[-1] + pd.Timedelta(
        minutes=5, seconds=SETTLEMENT_DELAY_SECONDS
    )
    now = int(base_close.timestamp())

    def loader(lake_dir, symbol, timeframe):
        if timeframe not in frames:
            raise FileNotFoundError(timeframe)
        return frames[timeframe].copy()

    monkeypatch.setattr(environment, "load_symbol_timeframe", loader)
    offline = environment.load_environment_inputs(cfg, "5m")

    class Client:
        def get_klines(self, symbol, interval, start=None, end=None, limit=1000):
            frame = frames[interval]
            frame = frame[
                (frame.index.asi8 / 1e9 >= start) & (frame.index.asi8 / 1e9 <= end)
            ].iloc[-limit:]
            return [
                [int(t.timestamp()), row.open, row.high, row.low, row.close, row.volume]
                for t, row in frame.iterrows()
            ]

    snapshot = build_live_feature_snapshot(Client(), cfg, now=now)
    np.testing.assert_array_equal(
        snapshot.feature_row,
        offline["features"].loc[base_close].to_numpy(dtype=np.float32),
    )
    assert snapshot.available_at == now
    assert all(value <= now for value in snapshot.timeframe_available_at.values())
    assert offline["timestamps"][-1] == now
    assert offline["bar_open_timestamps"][-1] == frames["5m"].index[-1]


def test_missing_declared_timeframe_fails_instead_of_changing_feature_composition(
    monkeypatch,
):
    cfg = load_app_config("config/app.yaml")

    def load(lake_dir, symbol, timeframe):
        if timeframe == "5m":
            return raw(100, 5)
        raise FileNotFoundError(timeframe)

    monkeypatch.setattr(environment, "load_symbol_timeframe", load)
    with pytest.raises(FileNotFoundError, match="missing required timeframe"):
        environment.load_environment_inputs(cfg, "5m")
    with pytest.raises(ValueError, match="base feature duration"):
        environment.load_environment_inputs(cfg, "1h")


@pytest.mark.parametrize("entrypoint", ["offline", "live"])
def test_named_hour_cannot_be_declared_closed_after_thirty_minutes(
    monkeypatch, entrypoint
):
    cfg = load_app_config("config/app.yaml")
    cfg.features.timeframes = [
        cfg.features.timeframes[0].model_copy(update={"name": "1h", "bar_minutes": 30})
    ]
    monkeypatch.setattr(
        environment,
        "load_symbol_timeframe",
        lambda *a: pytest.fail("must reject before lake read"),
    )

    class Client:
        def get_klines(self, *args, **kwargs):
            pytest.fail("must reject before vendor read")

    with pytest.raises(ValueError, match="interval name and bar_minutes disagree"):
        if entrypoint == "offline":
            environment.load_environment_inputs(cfg, "30m")
        else:
            build_live_feature_snapshot(Client(), cfg, now=1736000000)


@pytest.mark.parametrize("name,minutes", [("1h", 60), ("tf_5m", 5), ("1d", 1440)])
def test_consistent_named_durations_and_explicit_aliases_remain_supported(
    name, minutes
):
    cfg = load_app_config("config/app.yaml")
    cfg.features.timeframes = [
        cfg.features.timeframes[0].model_copy(
            update={"name": name, "bar_minutes": minutes}
        )
    ]
    assert active_timeframes(cfg.features)[0].bar_minutes == minutes
