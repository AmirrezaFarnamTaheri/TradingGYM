"""Depth-walk fill integration into ExecutionEngine."""

from decimal import Decimal

import pytest

from trading_gym.core.config import (
    BreakerConfig,
    CapacityConfig,
    CostConfig,
    EnvConfig,
    FinancingConfig,
    LeverageConfig,
    LiquidityConfig,
    ShortsConfig,
    SlippageConfig,
    VenueDecimals,
)
from trading_gym.execution.execution_engine import (
    ExecutionEngine,
    ExecutionState,
    MarketSnapshot,
)
from trading_gym.execution.fill_models import LimitOrderBook, PriceLevel


@pytest.fixture
def config():
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
            borrow_caps={"BTC": 50.0, "ETH": 10.0},
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


def _state(equity: float = 10_000.0) -> ExecutionState:
    return ExecutionState(
        position=Decimal("0"),
        equity=Decimal(str(equity)),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )


def _snapshot(book: LimitOrderBook | None = None, mid: float = 100.0) -> MarketSnapshot:
    return MarketSnapshot(
        mid_price=mid,
        spread_bps=20.0,
        volatility_hint=0.01,
        buy_cap_quote=float("inf"),
        sell_cap_quote=float("inf"),
        depth_book=book,
    )


def _asks(*levels: tuple[str, str]) -> tuple[PriceLevel, ...]:
    return tuple(PriceLevel(price=Decimal(p), quantity=Decimal(q)) for p, q in levels)


def test_buy_walks_asks_and_pays_depth_shortfall(config):
    book = LimitOrderBook(
        bids=_asks(("99.90", "5")),
        asks=_asks(("100.10", "1"), ("100.30", "2")),
    )
    engine = ExecutionEngine(config)
    result = engine.apply_trade(_state(), 3.0, _snapshot(book))

    assert result.delta_units == pytest.approx(Decimal("3"))
    # Quantity-weighted average across both consumed levels, tick-rounded.
    expected_avg = (
        Decimal("1") * Decimal("100.10") + Decimal("2") * Decimal("100.30")
    ) / Decimal("3")
    assert result.executed_price == expected_avg.quantize(Decimal("0.01"))
    assert result.executed_price > Decimal("100")
    # The book embeds the spread: crossing cost must stay zero and the
    # slippage bucket carries the realized depth shortfall vs mid.
    assert result.crossing_cost == Decimal("0")
    assert result.slippage_cost == pytest.approx(
        Decimal("3") * abs(result.executed_price - Decimal("100"))
    )
    assert result.fee_cost == pytest.approx(
        Decimal("3") * result.executed_price * Decimal("0.001")
    )
    assert not result.liquidity_capped
    assert result.state.average_entry_price == result.executed_price


def test_sell_hits_bids_below_mid(config):
    book = LimitOrderBook(
        bids=_asks(("99.80", "4"), ("99.60", "1")),
        asks=_asks(("100.20", "9")),
    )
    engine = ExecutionEngine(config)
    result = engine.apply_trade(_state(), -2.0, _snapshot(book))

    expected_avg = (Decimal("2") * Decimal("99.80")) / Decimal("2")
    assert result.executed_price == expected_avg.quantize(Decimal("0.01"))
    assert result.executed_price < Decimal("100")
    assert result.state.position == Decimal("-2")


def test_partial_book_liquidity_caps_the_fill(config):
    book = LimitOrderBook(
        bids=_asks(("99.90", "5")),
        asks=_asks(("100.10", "1.5")),
    )
    engine = ExecutionEngine(config)
    result = engine.apply_trade(_state(), 5.0, _snapshot(book))

    assert result.liquidity_capped
    assert result.delta_units == pytest.approx(Decimal("1.5"))
    assert result.state.position == Decimal("1.5")
    assert result.executed_price == Decimal("100.10")


def test_empty_side_refuses_the_trade_without_synthetic_fills(config):
    book = LimitOrderBook(bids=_asks(("99.90", "5")), asks=())
    engine = ExecutionEngine(config)
    result = engine.apply_trade(_state(), 2.0, _snapshot(book))

    assert result.delta_units == Decimal("0")
    assert result.state.position == Decimal("0")
    assert result.total_cost == Decimal("0")
    assert result.liquidity_capped


def test_no_book_keeps_legacy_parametric_path(config):
    engine = ExecutionEngine(config)
    market = _snapshot(None)
    result = engine.apply_trade(_state(), 1.0, market)

    half_spread = (Decimal("20") / Decimal("10000")) * Decimal("100") / Decimal("2")
    assert result.executed_price == (Decimal("100") + half_spread).quantize(
        Decimal("0.01")
    )
    assert result.crossing_cost == pytest.approx(Decimal("1") * half_spread)
    assert result.slippage_cost > 0
    assert not result.liquidity_capped


def test_depth_walk_is_deterministic_and_decimal_pure(config):
    book = LimitOrderBook(
        bids=_asks(("99.85", "3")),
        asks=_asks(("100.15", "2"), ("100.25", "3")),
    )
    engine = ExecutionEngine(config)
    first = engine.apply_trade(_state(), 4.0, _snapshot(book))
    second = engine.apply_trade(_state(), 4.0, _snapshot(book))

    assert first.executed_price == second.executed_price
    assert first.total_cost == second.total_cost
    for value in (
        first.state.equity,
        first.fee_cost,
        first.slippage_cost,
        first.executed_price,
    ):
        assert isinstance(value, Decimal)


def test_depth_path_respects_leverage_ceiling(config):
    # Equity 200 at max leverage 5 => 1000 quote of exposure => 10 BTC @ 100.
    book = LimitOrderBook(
        bids=_asks(("99.00", "500")),
        asks=_asks(("100.00", "500"), ("101.00", "500")),
    )
    engine = ExecutionEngine(config)
    result = engine.apply_trade(_state(200.0), 400.0, _snapshot(book))

    gross_exposure = abs(result.state.position) * result.executed_price
    ceiling = 5.0 * float(result.state.equity)
    assert float(gross_exposure) <= ceiling * 1.05
    assert abs(result.state.position) < 400.0


def test_malformed_levels_raise_instead_of_repricing(config):
    book = LimitOrderBook(
        bids=_asks(("99.90", "5")),
        asks=_asks(("100.10", "-2")),  # negative quantity is invalid
    )
    engine = ExecutionEngine(config)
    with pytest.raises(ValueError):
        engine.apply_trade(_state(), 2.0, _snapshot(book))
