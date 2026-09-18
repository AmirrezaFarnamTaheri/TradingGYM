"""Property-based invariant oracle for the Decimal execution engine.

Absorbed idea: an independent reference model cross-checks the governed
engine (cf. donor portfolio kernels used as accounting oracles).  Rather than
duplicating the engine's full cost model, these tests pin the invariants any
correct execution path must satisfy for arbitrary trade sequences.
"""

from decimal import Decimal

import numpy as np
import pytest

hypothesis = pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from trading_gym.execution.execution_engine import (  # noqa: E402
    ExecutionEngine,
    ExecutionState,
    MarketSnapshot,
)
from trading_gym.common.config import (  # noqa: E402
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

money = st.floats(min_value=1_000.0, max_value=1_000_000.0)
prices = st.floats(min_value=0.5, max_value=100_000.0)
targets = st.floats(
    min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False
)


def make_engine() -> ExecutionEngine:
    financing = FinancingConfig(daily_pct=0.05, periods_per_day=288)
    leverage = LeverageConfig(
        min_leverage=1.0,
        max_leverage=20.0,
        max_short_leverage=5.0,
        warning_margin_ratio=0.1,
        liquidation_margin_ratio=0.03,
        financing=financing,
    )
    cfg = EnvConfig(
        symbol="BTCUSDT",
        quote_ccy="USDT",
        decimals=VenueDecimals(price={"BTCUSDT": 2}, qty={"BTCUSDT": 6}),
        leverage=leverage,
        shorts=ShortsConfig(
            enabled=True, allowed_symbols=["BTCUSDT"], borrow_caps={"BTCUSDT": 25.0}
        ),
        costs=CostConfig(
            taker_fee_bps=10.0, dead_zone_equity_frac=0.001, max_spread_bps=80.0
        ),
        slippage=SlippageConfig(
            volatility_lambda=0.2,
            liquidity_lambda=0.2,
            stress_multiplier=1.0,
            volume_reference_fraction=0.01,
        ),
        liquidity=LiquidityConfig(default_quote_cap=250_000.0),
        capacity=CapacityConfig(conservative_default=10_000_000.0),
        breaker=BreakerConfig(max_loss_pct=99.0),
    )
    return ExecutionEngine(cfg)


def snapshot(price: float) -> MarketSnapshot:
    return MarketSnapshot(
        mid_price=price,
        spread_bps=12.0,
        volatility_hint=max(0.0, abs(price) % 0.01),
        buy_cap_quote=np.inf,
        sell_cap_quote=np.inf,
    )


@settings(
    max_examples=120, deadline=None, suppress_health_check=[HealthCheck.filter_too_much]
)
@given(equity=money, price=prices, target=targets)
def test_engine_preserves_wealth_identity(equity: float, price: float, target: float):
    engine = make_engine()
    state = ExecutionState(
        position=Decimal("0"),
        equity=Decimal(str(round(equity, 2))),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )
    result = engine.apply_trade(state, target, snapshot(price))

    # Wealth identity: equity changes only by the explicit cost terms.
    expected_equity = (
        state.equity - result.fee_cost - result.slippage_cost - result.crossing_cost
    )
    assert result.state.equity == expected_equity
    # Costs are never negative.
    assert (
        result.fee_cost >= 0 and result.slippage_cost >= 0 and result.crossing_cost >= 0
    )


@settings(max_examples=120, deadline=None)
@given(equity=money, price=prices, target=targets)
def test_executed_price_stays_inside_half_spread_band(
    equity: float, price: float, target: float
):
    engine = make_engine()
    state = ExecutionState(
        position=Decimal("0"),
        equity=Decimal(str(round(equity, 2))),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )
    market = snapshot(price)
    result = engine.apply_trade(state, target, market)

    if result.delta_units == 0:
        return
    # The engine rounds the executed price to the venue tick, so allow a
    # one-tick tolerance beyond the theoretical half-spread band.
    tick = Decimal("0.01")  # price_decimals=2 in the fixture config
    mid = Decimal(str(market.mid_price))
    half_spread = (Decimal(str(market.spread_bps)) / Decimal(10_000)) * mid / Decimal(2)
    lo, hi = min(mid - half_spread, mid + half_spread), max(
        mid - half_spread, mid + half_spread
    )
    assert lo - tick <= result.executed_price <= hi + tick


@settings(max_examples=100, deadline=None)
@given(equity=money, price=prices, target=targets)
def test_no_negative_balances_after_trade(equity: float, price: float, target: float):
    engine = make_engine()
    state = ExecutionState(
        position=Decimal("0"),
        equity=Decimal(str(round(equity, 2))),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )
    result = engine.apply_trade(state, target, snapshot(price))
    final = result.state
    assert final.equity >= 0
    assert final.borrowed_base >= 0
    assert final.borrowed_quote >= 0


@settings(max_examples=100, deadline=None)
@given(equity=money, price=prices, first=targets, second=targets)
def test_round_trip_never_increases_debt_beyond_borrow_cap(
    equity: float, price: float, first: float, second: float
):
    engine = make_engine()
    state = ExecutionState(
        position=Decimal("0"),
        equity=Decimal(str(round(equity, 2))),
        borrowed_base=Decimal("0"),
        borrowed_quote=Decimal("0"),
        average_entry_price=Decimal("0"),
    )
    snap = snapshot(price)
    after_first = engine.apply_trade(state, first, snap).state
    after_second = engine.apply_trade(after_first, second, snap)
    cap = Decimal(str(engine._short_cap)) if hasattr(engine, "_short_cap") else None
    if cap is not None:
        assert abs(after_second.state.borrowed_base) <= cap * Decimal("1.001")
