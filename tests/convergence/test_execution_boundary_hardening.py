from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from trading_gym.common.config import BorrowCapConfig, load_env_config
from trading_gym.execution.execution_engine import (
    ExecutionEngine,
    ExecutionState,
    MarketSnapshot,
)
from trading_gym.margin.borrow_caps import BorrowCapValidator, clip_short_quantity
from trading_gym.market.data_router import DataProviderRouter, MarketDataRequest


class Provider:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, request: MarketDataRequest) -> pd.DataFrame:
        self.calls += 1
        frequency = {"5m": "5min"}.get(request.timeframe, request.timeframe)
        index = pd.date_range(request.start, request.end, freq=frequency, tz="UTC")
        values = [100.0 + index for index in range(len(index))]
        return pd.DataFrame(
            {
                "open": values,
                "high": [value + 1 for value in values],
                "low": [value - 1 for value in values],
                "close": values,
                "volume": [1000.0] * len(values),
            },
            index=index,
        )


def _state() -> ExecutionState:
    return ExecutionState(
        position=Decimal("0"),
        equity=Decimal("10000"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )


def _market(**updates: float) -> MarketSnapshot:
    values = {
        "mid_price": 50000.0,
        "spread_bps": 10.0,
        "volatility_hint": 0.02,
        "buy_cap_quote": 100000.0,
        "sell_cap_quote": 100000.0,
    }
    values.update(updates)
    return MarketSnapshot(**values)


def test_execution_engine_rejects_invalid_market_and_state() -> None:
    engine = ExecutionEngine(load_env_config("config/app.yaml"))
    with pytest.raises(ValueError, match="mid price"):
        engine.apply_trade(_state(), 0.1, _market(mid_price=0.0))
    with pytest.raises(ValueError, match="non-finite"):
        engine.apply_trade(_state(), 0.1, _market(volatility_hint=np.nan))

    state = _state()
    state.borrowed_base = Decimal("-1")
    with pytest.raises(ValueError, match="negative balances"):
        engine.apply_trade(state, 0.1, _market())


def test_borrow_caps_use_suffix_parsing_and_reject_non_finite_quantities() -> None:
    config = BorrowCapConfig(
        enabled=True,
        allowed_symbols=["BTC"],
        borrow_caps={"BTC": 0.5},
    )
    validator = BorrowCapValidator(config)
    assert validator.validate_short("BTC-USDT", 0.4)[0] is True
    assert validator.validate_short("BTCUSDT", float("nan"))[1] == "INVALID_QUANTITY"
    assert validator.validate_short("USDTBTC", 0.1)[1] == "SYMBOL_NOT_ALLOWED"
    assert clip_short_quantity("BTC/USDT", 0.8, config) == 0.5


def test_market_data_cache_is_bounded_and_copy_isolated() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    provider = Provider()
    router = DataProviderRouter(cache_ttl_seconds=60, max_cache_entries=1)
    router.register("primary", provider)

    request_one = MarketDataRequest(
        instrument_id="BINANCE:BTCUSDT",
        timeframe="5m",
        start=start,
        end=start + timedelta(minutes=10),
        max_rows=3,
    )
    first = router.fetch(request_one)
    first.frame.iloc[0, first.frame.columns.get_loc("close")] = 999999.0
    cached = router.fetch(request_one)
    assert cached.from_cache is True
    assert cached.frame.iloc[0]["close"] != 999999.0

    request_two = request_one.model_copy(
        update={
            "instrument_id": "BINANCE:ETHUSDT",
            "start": start + timedelta(hours=1),
            "end": start + timedelta(hours=1, minutes=10),
        }
    )
    router.fetch(request_two)
    router.fetch(request_one)
    assert provider.calls == 3
