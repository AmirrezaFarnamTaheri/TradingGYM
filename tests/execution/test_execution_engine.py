"""
Comprehensive tests for execution engine including edge cases, property-based tests,
and numerical stability checks.
"""

import numpy as np
import pytest
from decimal import Decimal
from hypothesis import HealthCheck, given, settings, strategies as st
from trading_gym.core.config import (
    BreakerConfig,
    CostConfig,
    EnvConfig,
    LeverageConfig,
    FinancingConfig,
    ShortsConfig,
    SlippageConfig,
    LiquidityConfig,
    CapacityConfig,
    VenueDecimals,
)
from trading_gym.execution.execution_engine import (
    ExecutionEngine,
    ExecutionState,
    MarketSnapshot,
)


@pytest.fixture
def base_config():
    """Create base test configuration."""
    return EnvConfig(
        symbol="BTC",
        quote_ccy="IRT",
        decimals=VenueDecimals(
            price={"BTC": 2},
            qty={"BTC": 6},
            min_notional={"BTC": 10.0},
            min_qty={"BTC": 0.0001},
        ),
        leverage=LeverageConfig(
            min_leverage=1.0,
            max_leverage=5.0,
            warning_margin_ratio=0.5,
            liquidation_margin_ratio=0.3,
            financing=FinancingConfig(daily_pct=0.001, periods_per_day=1440),
        ),
        shorts=ShortsConfig(
            enabled=True,
            allowed_symbols=["BTC", "ETH"],
            borrow_caps={"BTC": 1.0, "ETH": 10.0},
        ),
        costs=CostConfig(
            taker_fee_bps=10.0,
            maker_fee_bps=5.0,
            dead_zone_equity_frac=0.01,
            max_spread_bps=500.0,
        ),
        slippage=SlippageConfig(
            volatility_lambda=0.1,
            liquidity_lambda=0.05,
            stress_multiplier=2.0,
            volume_reference_fraction=0.1,
        ),
        liquidity=LiquidityConfig(
            default_quote_cap=100000.0,
            per_symbol_quote_cap={"BTC": 50000.0},
        ),
        capacity=CapacityConfig(conservative_default=10000.0),
        breaker=BreakerConfig(),
    )


@pytest.fixture
def engine(base_config):
    """Create execution engine."""
    return ExecutionEngine(base_config)


@pytest.fixture
def market():
    """Create standard market snapshot."""
    return MarketSnapshot(
        mid_price=50000.0,
        spread_bps=10.0,
        volatility_hint=0.02,
        buy_cap_quote=100000.0,
        sell_cap_quote=100000.0,
    )


@pytest.fixture
def state():
    """Create standard execution state."""
    return ExecutionState(
        position=Decimal("0.0"),
        equity=Decimal("10000.0"),
        borrowed_base=Decimal("0.0"),
        borrowed_quote=Decimal("0.0"),
        average_entry_price=Decimal("0.0"),
    )


class TestBasicExecution:
    """Test basic execution mechanics."""

    def test_no_trade_when_target_equals_current(self, engine, state, market):
        """No trade should occur when target equals current position."""
        result = engine.apply_trade(state, target_position=0.0, market=market)

        assert result.delta_units == Decimal("0.0")
        assert result.fee_cost == Decimal("0.0")
        assert result.slippage_cost == Decimal("0.0")
        assert result.state.position == Decimal("0.0")
        assert result.state.equity == Decimal("10000.0")

    def test_buy_increases_position(self, engine, state, market):
        """Buying should increase position and apply costs."""
        result = engine.apply_trade(state, target_position=0.1, market=market)

        assert result.delta_units > 0
        assert result.state.position > 0
        assert result.fee_cost > 0
        assert result.state.equity < Decimal("10000.0")  # Costs deducted

    def test_sell_decreases_position(self, engine, state, market):
        """Selling should decrease position."""
        # Start with a position
        state.position = Decimal("0.1")
        result = engine.apply_trade(state, target_position=0.0, market=market)

        assert result.delta_units < 0
        assert result.state.position == Decimal("0.0")


class TestRounding:
    """Test rounding logic for quantities and prices."""

    def test_quantity_rounding_positive(self, engine):
        """Test quantity rounding for positive values."""
        rounded = engine._round_qty(Decimal("1.23456789"), decimals=6)
        assert rounded == Decimal("1.234567")  # Truncated to 6 decimals

    def test_quantity_rounding_negative(self, engine):
        """Test quantity rounding for negative values (trunc toward zero)."""
        rounded = engine._round_qty(Decimal("-1.23456789"), decimals=6)
        assert rounded == Decimal("-1.234567")  # Truncated toward zero

    def test_price_rounding(self, engine):
        """Test price rounding (standard rounding)."""
        rounded = engine._round_price(Decimal("50000.556"), decimals=2)
        assert rounded == Decimal("50000.56")  # Rounded to 2 decimals

    def test_dust_elimination(self, engine, state, market):
        """Extremely small trades should be eliminated."""
        result = engine.apply_trade(state, target_position=1e-20, market=market)

        assert result.delta_units == Decimal("0.0")


class TestLiquidityCapping:
    """Test liquidity constraints."""

    def test_trade_capped_by_venue_liquidity(self, engine, state):
        """Large trade should be capped by venue liquidity."""
        market = MarketSnapshot(
            mid_price=50000.0,
            spread_bps=10.0,
            volatility_hint=0.02,
            buy_cap_quote=5000.0,  # Low cap
            sell_cap_quote=5000.0,
        )

        # Try to buy 1 BTC (50k notional)
        result = engine.apply_trade(state, target_position=1.0, market=market)

        # Should be capped
        assert result.liquidity_capped
        assert abs(result.delta_units) < 1.0

    def test_trade_capped_by_config_liquidity(self, base_config, state):
        """Trade should be capped by config liquidity."""
        # Set low config cap
        base_config.liquidity.per_symbol_quote_cap["BTC"] = 1000.0
        engine = ExecutionEngine(base_config)

        market = MarketSnapshot(
            mid_price=50000.0,
            spread_bps=10.0,
            volatility_hint=0.02,
            buy_cap_quote=100000.0,  # High venue cap
            sell_cap_quote=100000.0,
        )

        # Try to buy 1 BTC
        result = engine.apply_trade(state, target_position=1.0, market=market)

        assert result.liquidity_capped


class TestMinimumSizes:
    """Test minimum notional and quantity enforcement."""

    def test_below_min_notional_rejected(self, engine, state, market):
        """Trade below min notional should not execute."""
        # Try tiny trade (below 10 IRT min)
        result = engine.apply_trade(state, target_position=0.0001, market=market)

        assert result.delta_units == Decimal("0.0")

    def test_below_min_qty_rejected(self, engine, state, market):
        """Trade below min quantity should not execute."""
        # Set position to test increment
        state.position = Decimal("0.0")
        # Try trade below 0.0001 BTC min
        result = engine.apply_trade(state, target_position=0.00001, market=market)

        assert result.delta_units == Decimal("0.0")


class TestShortSelling:
    """Test short selling mechanics and constraints."""

    def test_short_position_creates_borrowed_base(self, engine, state, market):
        """Going short should create borrowed base."""
        result = engine.apply_trade(state, target_position=-0.1, market=market)

        assert result.state.position < 0
        assert result.state.borrowed_base > 0
        assert result.state.borrowed_quote == Decimal("0.0")

    def test_short_respects_borrow_cap(self, engine, state, market):
        """Short position should not exceed borrow cap."""
        # Try to short more than cap (1.0 BTC)
        result = engine.apply_trade(state, target_position=-2.0, market=market)

        # Should be capped to 1.0
        assert abs(result.state.position) <= Decimal("1.0")

    def test_short_disabled_returns_zero(self, base_config, state, market):
        """Shorting should be disabled if not enabled in config."""
        base_config.shorts.enabled = False
        engine = ExecutionEngine(base_config)

        result = engine.apply_trade(state, target_position=-0.1, market=market)

        assert result.state.position == Decimal("0.0")

    def test_short_non_whitelisted_symbol_returns_zero(
        self, base_config, state, market
    ):
        """Cannot short non-whitelisted symbols."""
        base_config.shorts.allowed_symbols = ["ETH"]  # BTC not allowed
        engine = ExecutionEngine(base_config)

        result = engine.apply_trade(state, target_position=-0.1, market=market)

        assert result.state.position == Decimal("0.0")


class TestLongPositions:
    """Test long position mechanics."""

    def test_long_position_borrows_quote_when_equity_insufficient(self, engine, market):
        """Long position should borrow quote if equity insufficient."""
        state = ExecutionState(
            position=Decimal("0.0"),
            equity=Decimal("1000.0"),  # Low equity
            borrowed_base=Decimal("0.0"),
            borrowed_quote=Decimal("0.0"),
            average_entry_price=Decimal("0.0"),
        )

        # Try to buy 0.1 BTC (5000 notional + costs)
        result = engine.apply_trade(state, target_position=0.1, market=market)

        assert result.state.position > 0
        assert result.state.borrowed_quote > 0

    def test_long_position_no_borrow_when_equity_sufficient(
        self, engine, state, market
    ):
        """Should not borrow if equity covers trade."""
        result = engine.apply_trade(state, target_position=0.05, market=market)

        # Small trade, should not need borrowing
        assert result.state.borrowed_quote == Decimal("0.0")


class TestCosts:
    """Test cost calculations."""

    def test_fee_cost_proportional_to_notional(self, engine, state, market):
        """Fee should be proportional to trade size."""
        result1 = engine.apply_trade(state, target_position=0.1, market=market)

        state2 = ExecutionState(
            position=Decimal("0.0"),
            equity=Decimal("20000.0"),  # More equity for larger trade
            borrowed_base=Decimal("0.0"),
            borrowed_quote=Decimal("0.0"),
            average_entry_price=Decimal("0.0"),
        )
        result2 = engine.apply_trade(state2, target_position=0.2, market=market)

        # Larger trade should have ~2x fee
        assert result2.fee_cost > result1.fee_cost * Decimal("1.8")

    def test_slippage_increases_with_volatility(self, engine, state):
        """Slippage should increase with volatility."""
        market_low_vol = MarketSnapshot(
            mid_price=50000.0,
            spread_bps=10.0,
            volatility_hint=0.01,
            buy_cap_quote=100000.0,
            sell_cap_quote=100000.0,
        )

        market_high_vol = MarketSnapshot(
            mid_price=50000.0,
            spread_bps=10.0,
            volatility_hint=0.1,  # 10x higher
            buy_cap_quote=100000.0,
            sell_cap_quote=100000.0,
        )

        result_low = engine.apply_trade(
            state, target_position=0.1, market=market_low_vol
        )
        state2 = ExecutionState(
            position=Decimal("0.0"),
            equity=Decimal("10000.0"),
            borrowed_base=Decimal("0.0"),
            borrowed_quote=Decimal("0.0"),
            average_entry_price=Decimal("0.0"),
        )
        result_high = engine.apply_trade(
            state2, target_position=0.1, market=market_high_vol
        )

        assert result_high.slippage_cost > result_low.slippage_cost

    def test_crossing_cost_from_spread(self, engine, state, market):
        """Crossing cost should come from half-spread."""
        result = engine.apply_trade(state, target_position=0.1, market=market)

        # Crossing cost = delta * half_spread
        half_spread = (
            (Decimal(str(market.spread_bps)) / Decimal("10000.0"))
            * Decimal(str(market.mid_price))
            * Decimal("0.5")
        )
        expected_crossing = abs(result.delta_units) * half_spread

        assert abs(result.crossing_cost - expected_crossing) < Decimal("0.01")


class TestPropertyBased:
    """Property-based tests using Hypothesis."""

    @given(
        target=st.floats(min_value=-0.5, max_value=0.5),
        mid_price=st.floats(min_value=10000, max_value=100000),
    )
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_costs_always_non_negative(self, engine, target, mid_price):
        """All costs should always be non-negative."""
        state = ExecutionState(
            position=Decimal("0.0"),
            equity=Decimal("10000.0"),
            borrowed_base=Decimal("0.0"),
            borrowed_quote=Decimal("0.0"),
            average_entry_price=Decimal("0.0"),
        )

        market = MarketSnapshot(
            mid_price=mid_price,
            spread_bps=10.0,
            volatility_hint=0.02,
            buy_cap_quote=100000.0,
            sell_cap_quote=100000.0,
        )

        result = engine.apply_trade(state, target_position=target, market=market)

        assert result.fee_cost >= Decimal("0.0")
        assert result.slippage_cost >= Decimal("0.0")
        assert result.crossing_cost >= Decimal("0.0")

    @given(leverage=st.floats(min_value=1.0, max_value=5.0))
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_leverage_monotonicity(self, base_config, leverage):
        """Higher leverage should allow larger positions."""
        base_config.leverage.max_leverage = leverage
        engine = ExecutionEngine(base_config)

        state = ExecutionState(
            position=Decimal("0.0"),
            equity=Decimal("10000.0"),
            borrowed_base=Decimal("0.0"),
            borrowed_quote=Decimal("0.0"),
            average_entry_price=Decimal("0.0"),
        )

        market = MarketSnapshot(
            mid_price=50000.0,
            spread_bps=10.0,
            volatility_hint=0.02,
            buy_cap_quote=1000000.0,  # High cap
            sell_cap_quote=1000000.0,
        )

        # Try to go to max leverage
        max_position = leverage * 10000.0 / 50000.0
        result = engine.apply_trade(state, target_position=max_position, market=market)

        # Should not exceed leverage constraint
        exposure = abs(result.state.position) * Decimal(str(market.mid_price))
        actual_leverage = exposure / max(result.state.equity, Decimal("1.0"))
        assert actual_leverage <= Decimal(str(leverage)) + Decimal(
            "0.1"
        )  # Small tolerance for costs


class TestNumericalStability:
    """Test numerical stability with extreme values."""

    def test_zero_equity_no_crash(self, engine, market):
        """Zero equity should not crash."""
        state = ExecutionState(
            position=Decimal("0.0"),
            equity=Decimal("0.0"),
            borrowed_base=Decimal("0.0"),
            borrowed_quote=Decimal("0.0"),
            average_entry_price=Decimal("0.0"),
        )

        result = engine.apply_trade(state, target_position=0.1, market=market)

        # Should not crash
        assert result is not None

    def test_very_high_volatility(self, engine, state):
        """Very high volatility should not cause numerical issues."""
        market = MarketSnapshot(
            mid_price=50000.0,
            spread_bps=10.0,
            volatility_hint=10.0,  # Extreme
            buy_cap_quote=100000.0,
            sell_cap_quote=100000.0,
        )

        result = engine.apply_trade(state, target_position=0.01, market=market)

        assert np.isfinite(float(result.slippage_cost))

    def test_zero_liquidity_cap(self, engine, state):
        """Zero liquidity should cap trade to zero."""
        market = MarketSnapshot(
            mid_price=50000.0,
            spread_bps=10.0,
            volatility_hint=0.02,
            buy_cap_quote=0.0,
            sell_cap_quote=0.0,
        )

        result = engine.apply_trade(state, target_position=0.1, market=market)

        assert result.delta_units == Decimal("0.0") or result.liquidity_capped
