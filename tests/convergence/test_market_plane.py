from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from trading_gym.market.backtest_policy import (
    BacktestLimitError,
    validate_backtest_window,
)
from trading_gym.market.data_router import DataProviderRouter, MarketDataRequest
from trading_gym.market.models import AssetClass, InstrumentSpec
from trading_gym.market.registry import InstrumentConflict, InstrumentRegistry


def instrument(instrument_id: str, symbol: str, aliases: list[str]) -> InstrumentSpec:
    return InstrumentSpec(
        instrument_id=instrument_id,
        venue="BINANCE",
        symbol=symbol,
        asset_class=AssetClass.CRYPTO_SPOT,
        base_asset=symbol.replace("USDT", ""),
        quote_asset="USDT",
        aliases=aliases,
    )


def frame(start: datetime, rows: int = 3, *, invalid: bool = False) -> pd.DataFrame:
    index = pd.date_range(start=start, periods=rows, freq="5min", tz="UTC")
    values = [100.0 + index for index in range(rows)]
    result = pd.DataFrame(
        {
            "open": values,
            "high": [value + 1 for value in values],
            "low": [value - 1 for value in values],
            "close": values,
            "volume": [1000.0] * rows,
        },
        index=index,
    )
    if invalid:
        result.loc[index[0], "high"] = 1.0
    return result


class Provider:
    def __init__(self, output: pd.DataFrame | Exception) -> None:
        self.output = output
        self.calls = 0

    def fetch(self, request: MarketDataRequest) -> pd.DataFrame:
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def test_instrument_registry_protects_aliases_and_cas(tmp_path: Path) -> None:
    registry = InstrumentRegistry(tmp_path / "instruments.json")
    saved = registry.save(instrument("BINANCE:BTCUSDT", "BTCUSDT", ["BTC/USDT"]))
    assert saved["revision"] == 1
    assert registry.resolve("btc/usdt").instrument_id == "BINANCE:BTCUSDT"
    with pytest.raises(InstrumentConflict):
        registry.save(instrument("BINANCE:ETHUSDT", "ETHUSDT", ["BTC/USDT"]))
    with pytest.raises(InstrumentConflict):
        registry.save(
            instrument("BINANCE:BTCUSDT", "BTCUSDT", ["XBTUSDT"]),
            expected_revision=0,
        )


def test_backtest_policy_rejects_unbounded_windows() -> None:
    end = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with pytest.raises(BacktestLimitError) as error:
        validate_backtest_window(
            market="crypto",
            timeframe="1m",
            start=end - timedelta(days=365),
            end=end,
            warmup_bars=200,
        )
    assert error.value.details["allowed"] is False
    accepted = validate_backtest_window(
        market="crypto",
        timeframe="1d",
        start=end - timedelta(days=90),
        end=end,
        warmup_bars=20,
    )
    assert accepted["allowed"] is True


def test_market_router_fails_over_validates_and_caches() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    request = MarketDataRequest(
        instrument_id="BINANCE:BTCUSDT",
        timeframe="5m",
        start=start,
        end=start + timedelta(minutes=10),
        max_rows=3,
    )
    bad = Provider(frame(start, invalid=True))
    good = Provider(frame(start))
    router = DataProviderRouter(cache_ttl_seconds=60)
    router.register("bad", bad, priority=1, failure_threshold=1)
    router.register("good", good, priority=2)

    envelope = router.fetch(request)
    assert envelope.provider_id == "good"
    assert envelope.attempted_providers == ("bad", "good")
    assert envelope.row_count == envelope.expected_rows == 3
    assert envelope.completeness == 1.0
    cached = router.fetch(request)
    assert cached.from_cache is True
    assert good.calls == 1


def test_market_request_requires_aware_bounded_window() -> None:
    with pytest.raises(ValueError):
        MarketDataRequest(
            instrument_id="BINANCE:BTCUSDT",
            timeframe="1m",
            start=datetime(2026, 1, 1),
            end=datetime(2026, 1, 2),
        )
    with pytest.raises(ValueError):
        MarketDataRequest(
            instrument_id="BINANCE:BTCUSDT",
            timeframe="1m",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            max_rows=10,
        )


def _router_request():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return start, MarketDataRequest(
        instrument_id="BINANCE:BTCUSDT",
        timeframe="5m",
        start=start,
        end=start + timedelta(minutes=15),
        max_rows=4,
    )


def test_balanced_market_router_uses_complete_secondary_before_degraded_primary():
    start, request = _router_request()
    sparse = Provider(frame(start, 2))
    complete = Provider(frame(start, 4))
    router = DataProviderRouter(routing_mode="balanced", target_completeness=0.95)
    router.register("sparse", sparse, priority=1)
    router.register("complete", complete, priority=2)
    result = router.fetch(request)
    assert result.provider_id == "complete"
    assert result.completeness == 1.0
    assert result.attempted_providers == ("sparse", "complete")


def test_fast_market_router_uses_first_valid_provider_to_minimize_latency():
    start, request = _router_request()
    sparse = Provider(frame(start, 2))
    complete = Provider(frame(start, 4))
    router = DataProviderRouter(routing_mode="fast", target_completeness=0.95)
    router.register("sparse", sparse, priority=1)
    router.register("complete", complete, priority=2)
    result = router.fetch(request)
    assert result.provider_id == "sparse"
    assert result.completeness == 0.5
    assert "coverage below configured quality target" in result.warnings
    assert complete.calls == 0


def test_quality_market_router_prefers_best_available_coverage():
    start, request = _router_request()
    first, better, failed = (
        Provider(frame(start, 2)),
        Provider(frame(start, 3)),
        Provider(OSError("down")),
    )
    router = DataProviderRouter(routing_mode="quality", cache_ttl_seconds=120)
    router.register("failed", failed, priority=1)
    router.register("first", first, priority=2)
    router.register("better", better, priority=3)
    result = router.fetch(request)
    assert result.provider_id == "better"
    assert result.completeness == 0.75
    assert result.attempted_providers == ("failed", "first", "better")
    assert "coverage below configured quality target" in result.warnings


def test_market_routing_mode_rejects_invalid_quality_policy():
    for kwargs in (
        {"routing_mode": "surprise"},
        {"target_completeness": float("nan")},
        {"target_completeness": 1.1},
    ):
        with pytest.raises(ValueError):
            DataProviderRouter(**kwargs)
