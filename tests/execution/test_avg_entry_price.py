import pytest
from decimal import Decimal
from trading_gym.execution.execution_engine import (
    ExecutionEngine,
    ExecutionState,
    MarketSnapshot,
)
from pydantic import BaseModel, Field
from typing import Dict, List


# Mock Config Structures using Pydantic to mimic Config classes
class MockDecimals(BaseModel):
    def qty_for(self, s):
        return 8

    def price_for(self, s):
        return 2

    def min_qty_for(self, s):
        return 0.0001

    def min_notional_for(self, s):
        return 5.0


class MockCosts(BaseModel):
    taker_fee_bps: float = 0.0  # ZERO FEES FOR TESTING LOGIC


class MockSlippage(BaseModel):
    volatility_lambda: float = 0.0
    liquidity_lambda: float = 0.0


class MockLeverage(BaseModel):
    max_leverage: float = 10.0
    max_short_leverage: float = 3.0
    contract_sizes: Dict[str, float] = {}
    margin_rates: Dict[str, float] = {}


class MockShorts(BaseModel):
    enabled: bool = True
    allowed_symbols: List[str] = ["BTC"]
    borrow_caps: Dict[str, float] = {"BTC": 100.0}


class MockLiquidity(BaseModel):
    def cap_for(self, s):
        return 1000000.0


class MockConfig(BaseModel):
    symbol: str = "BTCUSDT"
    quote_ccy: str = "USDT"
    decimals: MockDecimals = Field(default_factory=MockDecimals)
    costs: MockCosts = Field(default_factory=MockCosts)
    slippage: MockSlippage = Field(default_factory=MockSlippage)
    leverage: MockLeverage = Field(default_factory=MockLeverage)
    shorts: MockShorts = Field(default_factory=MockShorts)
    liquidity: MockLiquidity = Field(default_factory=MockLiquidity)


@pytest.fixture
def engine():
    cfg = MockConfig()
    return ExecutionEngine(cfg)  # type: ignore


def test_average_entry_price_increase_long(engine):
    # Initial: +1.0 BTC @ 50,000
    # Increase equity to 20,000 to avoid leverage caps (10x limit)
    state = ExecutionState(
        position=Decimal("1.0"),
        equity=Decimal("20000.0"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("40000"),
        average_entry_price=Decimal("50000.0"),
    )

    market = MarketSnapshot(
        mid_price=60000.0,
        spread_bps=0.0,
        volatility_hint=0.0,
        buy_cap_quote=1e9,
        sell_cap_quote=1e9,
    )

    # Buy 1.0 more @ 60,000
    result = engine.apply_trade(state, 2.0, market)

    # Check execution logic
    assert result.delta_units == Decimal("1.0")
    assert result.executed_price == Decimal("60000.0")

    # New Avg = (1.0 * 50000 + 1.0 * 60000) / 2.0 = 55000
    assert result.state.average_entry_price == Decimal("55000.0")
    assert result.state.position == Decimal("2.0")


def test_average_entry_price_decrease_long(engine):
    # Initial: +2.0 BTC @ 55,000
    state = ExecutionState(
        position=Decimal("2.0"),
        equity=Decimal("20000.0"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("55000.0"),
    )

    market = MarketSnapshot(
        mid_price=60000.0,
        spread_bps=0.0,
        volatility_hint=0.0,
        buy_cap_quote=1e9,
        sell_cap_quote=1e9,
    )

    # Sell 1.0, reduce to 1.0
    result = engine.apply_trade(state, 1.0, market)

    assert result.delta_units == Decimal("-1.0")

    # Avg Price should NOT change on reduction
    assert result.state.average_entry_price == Decimal("55000.0")
    assert result.state.position == Decimal("1.0")


def test_average_entry_price_flip_long_to_short(engine):
    # Initial: +1.0 BTC @ 50,000
    state = ExecutionState(
        position=Decimal("1.0"),
        equity=Decimal("20000.0"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("50000.0"),
    )

    market = MarketSnapshot(
        mid_price=40000.0,
        spread_bps=0.0,
        volatility_hint=0.0,
        buy_cap_quote=1e9,
        sell_cap_quote=1e9,
    )

    # Sell 2.0, flip to -1.0
    result = engine.apply_trade(state, -1.0, market)

    assert result.delta_units == Decimal("-2.0")
    assert result.state.position == Decimal("-1.0")

    # New Avg should be the execution price of the new position (40,000)
    assert result.state.average_entry_price == Decimal("40000.0")


def test_average_entry_price_increase_short(engine):
    # Initial: -1.0 BTC @ 50,000
    # Increase equity to 50,000. Max Short Lev is 3.0.
    # Max Exp = 150,000.
    # Target: -2.0 @ 60,000 = 120,000. OK.
    state = ExecutionState(
        position=Decimal("-1.0"),
        equity=Decimal("50000.0"),
        borrowed_base=Decimal("1.0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("50000.0"),
    )

    market = MarketSnapshot(
        mid_price=60000.0,
        spread_bps=0.0,
        volatility_hint=0.0,
        buy_cap_quote=1e9,
        sell_cap_quote=1e9,
    )

    # Short 1.0 more @ 60,000 (Target -2.0)
    result = engine.apply_trade(state, -2.0, market)

    assert result.delta_units == Decimal("-1.0")
    assert result.state.position == Decimal("-2.0")

    # New Avg = (1.0 * 50000 + 1.0 * 60000) / 2.0 = 55000
    assert result.state.average_entry_price == Decimal("55000.0")


def test_average_entry_price_open_fresh(engine):
    state = ExecutionState(
        position=Decimal("0"),
        equity=Decimal("20000.0"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )

    market = MarketSnapshot(
        mid_price=50000.0,
        spread_bps=0.0,
        volatility_hint=0.0,
        buy_cap_quote=1e9,
        sell_cap_quote=1e9,
    )

    result = engine.apply_trade(state, 1.0, market)

    assert result.state.average_entry_price == Decimal("50000.0")
