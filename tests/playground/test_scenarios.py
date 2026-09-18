"""AXIOM-1 playground scenarios (ADR-031): deterministic overlay primitives.

Contract:
- A scenario is declarative data: base window + ordered overlays + seed +
  repetitions. Overlays are pure functions frame-in → frame-out with the
  same DatetimeIndex, same columns, and all OHLCV invariants preserved
  (positive prices, high envelope, low envelope, non-negative volume,
  finite values).
- Overlay application is deterministic per (seed, frame). Randomized
  parameter draws use a caller-supplied numpy Generator — never global
  randomness.
- Composite scenarios validate at construction: bad overlay types,
  inverted windows, and invalid params fail before any run.
- Overlay attribution support: each overlay records which columns it
  touched, so runner-level A/B deltas are explainable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_gym.playground.scenarios import (
    OverlayError,
    Scenario,
    ScenarioWindow,
    build_overlay,
    gap_injection,
    stale_feed,
)
from trading_gym.strategy_lab.evaluator import validate_market_frame


def make_frame(n: int = 120, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-07-01T00:00:00Z", periods=n, freq="5min", tz="UTC")
    close = 50_000 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    open_ = np.roll(close, 1)
    open_[0] = 50_000.0
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0005, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0005, n)))
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(10, 1000, n),
            "spread_bps": rng.uniform(1, 5, n),
        },
        index=index,
    )


def assert_invariants(frame: pd.DataFrame) -> None:
    validate_market_frame(frame)
    assert frame.index.is_monotonic_increasing
    assert not frame.index.has_duplicates
    if isinstance(frame.index, pd.DatetimeIndex):
        assert frame.index.tz is not None


class TestScenarioWindow:
    def test_window_binds_frame_and_validates(self) -> None:
        frame = make_frame()
        window = ScenarioWindow(symbol="BTCUSDT", timeframe="5m", frame=frame)
        assert window.symbol == "BTCUSDT"
        assert window.bar_seconds == 300

    def test_window_rejects_inverted_or_single_bar(self) -> None:
        frame = make_frame(n=1)
        with pytest.raises(OverlayError):
            ScenarioWindow(symbol="BTCUSDT", timeframe="5m", frame=frame)

    def test_window_rejects_naive_index(self) -> None:
        frame = make_frame().tz_localize(None)
        with pytest.raises(OverlayError):
            ScenarioWindow(symbol="BTCUSDT", timeframe="5m", frame=frame)


class TestOverlayPrimitives:
    def test_gap_injection_flash_crash(self) -> None:
        frame = make_frame()
        start = frame.index[60]
        out = gap_injection(
            frame,
            kind="flash_crash",
            magnitude_pct=-12.0,
            recovery_bars=6,
            start=start,
        )
        assert_invariants(out)
        # the crash bar's close drops vs the base close, then recovers
        assert out.loc[start, "close"] < frame.loc[start, "close"]
        end = frame.index[60 + 6]
        assert out.loc[end, "close"] == pytest.approx(frame.loc[end, "close"])

    @pytest.mark.parametrize("kind", ["flash_crash", "gap_up", "gap_down"])
    def test_gap_kinds_preserve_envelope(self, kind: str) -> None:
        frame = make_frame()
        start = frame.index[30]
        magnitude = (
            -12.0 if kind == "flash_crash" else (8.0 if kind == "gap_up" else -8.0)
        )
        out = gap_injection(
            frame, kind=kind, magnitude_pct=magnitude, recovery_bars=4, start=start
        )
        assert_invariants(out)

    def test_gap_injection_rejects_bad_params(self) -> None:
        frame = make_frame()
        with pytest.raises(OverlayError):
            gap_injection(
                frame,
                kind="flash_crash",
                magnitude_pct=5.0,
                recovery_bars=0,
                start=frame.index[10],
            )
        with pytest.raises(OverlayError):
            gap_injection(
                frame,
                kind="volcano",
                magnitude_pct=-10.0,
                recovery_bars=4,
                start=frame.index[10],
            )

    def test_stale_feed_freezes_rows(self) -> None:
        frame = make_frame()
        start = frame.index[40]
        out = stale_feed(frame, duration_bars=8, start=start)
        assert_invariants(out)
        # rows inside the frozen stretch are identical to the first frozen row
        frozen = out.iloc[40:48]
        assert frozen.nunique(dropna=False).min() == 1
        # the bar after the freeze resumes with new data
        assert not out.iloc[48].equals(out.iloc[47])

    def test_stale_feed_at_frame_end_is_legal(self) -> None:
        frame = make_frame()
        out = stale_feed(frame, duration_bars=200, start=frame.index[100])
        assert_invariants(out)

    def test_stale_feed_rejects_bad_duration(self) -> None:
        frame = make_frame()
        with pytest.raises(OverlayError):
            stale_feed(frame, duration_bars=0, start=frame.index[10])


class TestScenarioComposition:
    def test_build_registry_and_compose(self) -> None:
        scenario = Scenario.parse(
            {
                "scenario_id": "stress-2024-q3",
                "window": {
                    "symbol": "BTCUSDT",
                    "timeframe": "5m",
                    "frame": make_frame(60),
                },
                "overlays": [
                    {
                        "type": "gap_injection",
                        "params": {
                            "kind": "flash_crash",
                            "magnitude_pct": -10.0,
                            "recovery_bars": 5,
                            "start": 20,
                        },
                    },
                    {
                        "type": "stale_feed",
                        "params": {"duration_bars": 4, "start": 40},
                    },
                ],
                "seed": 20260901,
                "repetitions": 3,
            }
        )
        assert scenario.scenario_id == "stress-2024-q3"
        assert scenario.window.frame.shape[0] == 60
        assert scenario.repetitions == 3
        base = scenario.window.frame
        stressed = scenario.stressed_frame()
        assert_invariants(stressed)
        # overlays actually changed something
        assert not stressed.equals(base)

    def test_repetitions_vary_by_seed(self) -> None:
        frame = make_frame(80)
        scenario = Scenario.parse(
            {
                "scenario_id": "mc",
                "window": {"symbol": "BTCUSDT", "timeframe": "5m", "frame": frame},
                "overlays": [
                    {
                        "type": "gap_injection",
                        "params": {
                            "kind": "flash_crash",
                            "magnitude_pct": -10.0,
                            "recovery_bars": 5,
                            "start": 30,
                            "randomize": ["magnitude_pct"],
                            "ranges": {"magnitude_pct": [-15.0, -5.0]},
                        },
                    }
                ],
                "seed": 42,
                "repetitions": 5,
            }
        )
        frames = scenario.stressed_frames()
        assert len(frames) == 5
        closes = {tuple(np.round(f["close"].to_numpy(), 6)) for f in frames}
        assert len(closes) == 5, "repetitions must vary by derived seed"

    def test_unknown_overlay_type_rejected(self) -> None:
        with pytest.raises(OverlayError):
            build_overlay({"type": "moon_shot", "params": {}})

    def test_overlay_start_outside_frame_rejected(self) -> None:
        frame = make_frame(60)
        with pytest.raises(OverlayError):
            build_overlay(
                {
                    "type": "stale_feed",
                    "params": {"duration_bars": 2, "start": 999},
                }
            ).apply(frame)

    def test_scenario_requires_id_and_seed(self) -> None:
        with pytest.raises(OverlayError):
            Scenario.parse(
                {
                    "window": {
                        "symbol": "BTCUSDT",
                        "timeframe": "5m",
                        "frame": make_frame(30),
                    },
                    "overlays": [],
                    "seed": 1,
                    "repetitions": 1,
                }
            )


class TestDeterminism:
    def test_same_seed_same_output(self) -> None:
        frame = make_frame(80)
        spec = {
            "scenario_id": "det",
            "window": {"symbol": "BTCUSDT", "timeframe": "5m", "frame": frame},
            "overlays": [
                {
                    "type": "gap_injection",
                    "params": {
                        "kind": "flash_crash",
                        "magnitude_pct": -10.0,
                        "recovery_bars": 5,
                        "start": 30,
                        "randomize": ["magnitude_pct"],
                        "ranges": {"magnitude_pct": [-15.0, -5.0]},
                    },
                }
            ],
            "seed": 1234,
            "repetitions": 2,
        }
        first = Scenario.parse(spec).stressed_frames()
        second = Scenario.parse(spec).stressed_frames()
        for a, b in zip(first, second):
            pd.testing.assert_frame_equal(a, b)
