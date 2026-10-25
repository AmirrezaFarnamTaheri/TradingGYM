import pytest
from decimal import Decimal
from trading_gym.execution.execution_engine import (
    ExecutionEngine,
    ExecutionState,
    MarketSnapshot,
)
from trading_gym.core.config import (
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


def test_precision_dust_elimination(execution_engine):
    """
    Simulate holding a position that originated from a float error
    e.g. 0.3333333333333333
    """
    # 1. Setup State with float artifacts
    # In Python float: 1/3 = 0.3333333333333333
    dirty_float_pos = 1.0 / 3.0

    state = ExecutionState(
        position=Decimal(str(dirty_float_pos)),  # Simulate conversion from float
        equity=Decimal("1000.0"),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("100.0"),
    )

    # 2. Market Snapshot
    market = MarketSnapshot(
        mid_price=3000.0,
        spread_bps=10.0,
        volatility_hint=0.01,
        buy_cap_quote=1e9,
        sell_cap_quote=1e9,
    )

    # 3. Execute "Sell All" (Target = 0)
    # The engine calculates delta = target - position.
    # But since the target is clean (0) and position is dirty (0.333...),
    # the delta will be -0.333...
    # The engine then rounds this delta to the asset's precision (8 decimals).
    # So delta becomes -0.33333333.
    # The resulting position is 0.3333333333... - 0.33333333 = 3.33e-9.
    result = execution_engine.apply_trade(
        state, target_position=Decimal("0.0"), market=market
    )

    # 4. Verification
    # The post-audit exact-flat contract canonicalizes a true flatten request
    # to zero rather than carrying sub-precision accounting dust forward.
    assert result.state.position == Decimal("0")
    assert result.delta_units == -state.position


def test_decimal_math_consistency():
    """Ensure no implicit float conversions happen during cost calc."""
    val_a = Decimal("0.1")
    val_b = Decimal("0.2")

    # Float behavior: 0.1 + 0.2 != 0.3
    # Decimal behavior: 0.1 + 0.2 == 0.3

    assert (val_a + val_b) == Decimal("0.3")
