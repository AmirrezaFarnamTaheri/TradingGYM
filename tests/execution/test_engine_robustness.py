import pytest
from decimal import Decimal
from trading_gym.execution.execution_engine import (
    ExecutionEngine,
    ExecutionState,
    MarketSnapshot,
)
from trading_gym.common.config import (
    EnvConfig,
    VenueDecimals,
    LeverageConfig,
    ShortsConfig,
    CostConfig,
    SlippageConfig,
    LiquidityConfig,
    CapacityConfig,
    BreakerConfig,
    FinancingConfig,
    RewardConfig,
)


@pytest.fixture
def engine_decimal_config():
    """Provides a complete EnvConfig for the ExecutionEngine."""
    financing = FinancingConfig(daily_pct=0.05, periods_per_day=288)
    leverage = LeverageConfig(
        min_leverage=1.0,
        max_leverage=20.0,
        max_short_leverage=5.0,
        warning_margin_ratio=0.1,
        liquidation_margin_ratio=0.03,
        financing=financing,
    )

    return EnvConfig(
        symbol="BTCUSDT",
        quote_ccy="USDT",
        decimals=VenueDecimals(
            price={"BTCUSDT": 2},
            qty={"BTCUSDT": 8},
            min_notional={"BTCUSDT": 10.0},
            min_qty={"BTCUSDT": 0.0001},
        ),
        leverage=leverage,
        shorts=ShortsConfig(
            enabled=True, allowed_symbols=["BTCUSDT"], borrow_caps={"BTCUSDT": 10.0}
        ),
        costs=CostConfig(
            taker_fee_bps=5.0, dead_zone_equity_frac=0.001, max_spread_bps=50.0
        ),
        # Default slippage config, will be overridden in specific tests
        slippage=SlippageConfig(
            volatility_lambda=0.1,
            liquidity_lambda=0.1,
            stress_multiplier=1.0,
            volume_reference_fraction=0.01,
        ),
        liquidity=LiquidityConfig(default_quote_cap=1000000.0),
        capacity=CapacityConfig(conservative_default=10000000.0),
        breaker=BreakerConfig(),
        rewards=RewardConfig(),
    )


@pytest.fixture
def execution_engine(engine_decimal_config):
    return ExecutionEngine(engine_decimal_config)


def test_engine_handles_nan_target(execution_engine):
    state = ExecutionState(
        position=Decimal("0"),
        equity=Decimal("1000"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )
    market = MarketSnapshot(
        mid_price=100.0,
        spread_bps=10.0,
        volatility_hint=0.01,
        buy_cap_quote=1e6,
        sell_cap_quote=1e6,
    )

    result = execution_engine.apply_trade(
        state, target_position=float("nan"), market=market
    )
    assert result.delta_units == Decimal("0")
    assert result.state.position == Decimal("0")


def test_engine_handles_inf_target(execution_engine):
    state = ExecutionState(
        position=Decimal("0"),
        equity=Decimal("1000"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )
    market = MarketSnapshot(
        mid_price=100.0,
        spread_bps=10.0,
        volatility_hint=0.01,
        buy_cap_quote=1e6,
        sell_cap_quote=1e6,
    )

    result = execution_engine.apply_trade(
        state, target_position=float("inf"), market=market
    )
    assert result.delta_units == Decimal("0")


def test_engine_handles_invalid_type(execution_engine):
    state = ExecutionState(
        position=Decimal("0"),
        equity=Decimal("1000"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )
    market = MarketSnapshot(
        mid_price=100.0,
        spread_bps=10.0,
        volatility_hint=0.01,
        buy_cap_quote=1e6,
        sell_cap_quote=1e6,
    )

    result = execution_engine.apply_trade(state, target_position="invalid", market=market)  # type: ignore
    assert result.delta_units == Decimal("0")


def test_max_leverage_capping(engine_decimal_config):
    # Disable liquidity slippage for this test to isolate leverage logic
    engine_decimal_config.slippage.liquidity_lambda = 0.0
    engine_decimal_config.slippage.volatility_lambda = 0.0

    execution_engine = ExecutionEngine(engine_decimal_config)

    # Config allows max 20x for longs (Abantether Credit Trading)
    state = ExecutionState(
        position=Decimal("0"),
        equity=Decimal("1000"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )
    # Zero volatility and huge liquidity to prevent slippage-induced bankruptcy
    market = MarketSnapshot(
        mid_price=100.0,
        spread_bps=0.0,
        volatility_hint=0.0,
        buy_cap_quote=1e12,
        sell_cap_quote=1e12,
    )

    # Try to buy 500 BTC (50,000 notional) -> 50x leverage
    result = execution_engine.apply_trade(state, target_position=500.0, market=market)

    # Should be capped at roughly 200 BTC (20x * 1000 / 100)
    # With 0 slippage and 5bps fee:
    # Initial Notional = 50,000. Fee = 25. Equity = 975.
    # Max Units = (975 * 20) / 100 = 195.

    # So position should be approximately 190-200 units
    assert result.state.position < Decimal("200.1")
    assert result.state.position > Decimal("190.0")


def test_short_cap_zero_equity(execution_engine):
    # Zero equity -> no trading allowed
    state = ExecutionState(
        position=Decimal("0"),
        equity=Decimal("0"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )
    market = MarketSnapshot(
        mid_price=100.0,
        spread_bps=10.0,
        volatility_hint=0.01,
        buy_cap_quote=1e9,
        sell_cap_quote=1e9,
    )

    result = execution_engine.apply_trade(state, target_position=1.0, market=market)
    assert result.delta_units == Decimal("0")
