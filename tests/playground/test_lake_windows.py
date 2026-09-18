"""AXIOM-1 lake-backed playground windows + regime arm (round 5).

LakeWindowProvider contract:
- Wraps ``data.data_loader.load_symbol_timeframe`` behind the playground
  window seam: resolve(symbol, timeframe, start, end) → validated frame
  slice with tz-aware DatetimeIndex and full OHLCV validation.
- Slice discipline: start/end must be tz-aware, start < end, and the
  resolved slice keeps the frame's invariants (validate_market_frame).
- Missing partition → the provider raises FileNotFoundError (fail-closed,
  never returns an empty frame that downstream code might misread as a
  flat market).

RegimeArm contract:
- Classifies the recent-returns window into one of three regimes —
  trend_up, trend_down, chop — deterministically (pure function of the
  returns tuple; no state, no randomness).
- Trend when |sum| exceeds the threshold AND directional consistency
  holds; chop otherwise. Output feeds the fusion engine's regime tag.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading_gym.agent.arms.factory import RegimeArmBackend
from trading_gym.playground.lake_windows import LakeWindowProvider
from trading_gym.strategy_lab.evaluator import validate_market_frame


def _lake_with_partition(tmp_path: Path, n: int = 240) -> Path:
    lake_dir = tmp_path / "lake" / "BTCUSDT" / "5m"
    lake_dir.mkdir(parents=True)
    rng = np.random.default_rng(11)
    index = pd.date_range("2024-07-01", periods=n, freq="5min", tz="UTC")
    close = 50_000 * np.exp(np.cumsum(rng.normal(0.0002, 0.0015, n)))
    open_ = np.roll(close, 1)
    open_[0] = 50_000.0
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0004, n)))
    frame = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(10, 1000, n),
            "spread_bps": np.full(n, 2.0),
        },
        index=index,
    )
    frame.to_parquet(lake_dir / "part-0000.parquet")
    return tmp_path / "lake"


class TestLakeWindowProvider:
    def test_resolves_slice_with_invariants(self, tmp_path: Path) -> None:
        lake = _lake_with_partition(tmp_path)
        provider = LakeWindowProvider(lake)
        frame = provider.resolve(
            symbol="BTCUSDT",
            timeframe="5m",
            start="2024-07-01T00:00:00Z",
            end="2024-07-01T10:00:00Z",
        )
        validate_market_frame(frame)
        assert frame.index.tz is not None
        assert len(frame) > 0

    def test_slice_bounds_are_honored(self, tmp_path: Path) -> None:
        lake = _lake_with_partition(tmp_path)
        provider = LakeWindowProvider(lake)
        frame = provider.resolve(
            symbol="BTCUSDT",
            timeframe="5m",
            start="2024-07-01T02:00:00Z",
            end="2024-07-01T08:00:00Z",
        )
        assert frame.index[0] >= pd.Timestamp("2024-07-01T02:00:00Z")
        assert frame.index[-1] <= pd.Timestamp("2024-07-01T08:00:00Z")

    def test_missing_partition_fails_closed(self, tmp_path: Path) -> None:
        lake = _lake_with_partition(tmp_path)
        provider = LakeWindowProvider(lake)
        with pytest.raises(FileNotFoundError):
            provider.resolve(
                symbol="SOLUSDT",
                timeframe="5m",
                start="2024-07-01T00:00:00Z",
                end="2024-07-01T01:00:00Z",
            )

    def test_naive_timestamps_rejected(self, tmp_path: Path) -> None:
        lake = _lake_with_partition(tmp_path)
        provider = LakeWindowProvider(lake)
        with pytest.raises(ValueError):
            provider.resolve(
                symbol="BTCUSDT",
                timeframe="5m",
                start="2024-07-01T00:00:00",  # naive
                end="2024-07-01T01:00:00Z",
            )

    def test_inverted_window_rejected(self, tmp_path: Path) -> None:
        lake = _lake_with_partition(tmp_path)
        provider = LakeWindowProvider(lake)
        with pytest.raises(ValueError):
            provider.resolve(
                symbol="BTCUSDT",
                timeframe="5m",
                start="2024-07-01T05:00:00Z",
                end="2024-07-01T04:00:00Z",
            )

    def test_window_too_short_rejected(self, tmp_path: Path) -> None:
        lake = _lake_with_partition(tmp_path)
        provider = LakeWindowProvider(lake)
        with pytest.raises(ValueError):
            provider.resolve(
                symbol="BTCUSDT",
                timeframe="5m",
                start="2024-07-01T00:00:00Z",
                end="2024-07-01T00:05:00Z",  # 2 bars: below playground minimum
            )

    def test_unknown_timeframe_rejected(self, tmp_path: Path) -> None:
        lake = _lake_with_partition(tmp_path)
        provider = LakeWindowProvider(lake)
        with pytest.raises(ValueError):
            provider.resolve(
                symbol="BTCUSDT",
                timeframe="2m",
                start="2024-07-01T00:00:00Z",
                end="2024-07-01T01:00:00Z",
            )


class TestRegimeArm:
    def test_uptrend_classification(self) -> None:
        arm = RegimeArmBackend()
        assert arm.classify((0.01, 0.008, 0.012, 0.009, 0.011)) == "trend_up"

    def test_downtrend_classification(self) -> None:
        arm = RegimeArmBackend()
        assert arm.classify((-0.01, -0.008, -0.012, -0.009, -0.011)) == "trend_down"

    def test_chop_classification(self) -> None:
        arm = RegimeArmBackend()
        assert arm.classify((0.01, -0.01, 0.01, -0.01, 0.005)) == "chop"

    def test_empty_returns_are_chop(self) -> None:
        arm = RegimeArmBackend()
        assert arm.classify(()) == "chop"

    def test_arm_proposal_carries_regime(self) -> None:
        arm = RegimeArmBackend()
        from trading_gym.intelligence.types import MarketContext

        context = MarketContext(
            symbol="BTCUSDT",
            position_units=0.0,
            equity=10_000.0,
            margin_level=None,
            last_close=50_000.0,
            prev_close=49_500.0,
            recent_returns=(0.01, 0.008, 0.012, 0.009),
            drawdown_from_peak=0.03,
        )
        signal = arm.decide(context)
        assert signal.backend == "regime"
        assert "trend_up" in signal.rationale
        assert signal.action_kind is not None

    def test_registered_in_default_roster(self) -> None:
        from trading_gym.agent.arms.factory import default_registry

        registry = default_registry()
        assert "regime" in [arm.name for arm in registry.arms()]
