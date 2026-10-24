from decimal import Decimal as D
from pathlib import Path

from hypothesis import given, settings, strategies as st

from trading_gym.common.config import load_app_config
from trading_gym.execution.execution_engine import ExecutionEngine, ExecutionState, MarketSnapshot
from trading_gym.execution.fill_models import LimitOrderBook, PriceLevel

ROOT = Path(__file__).resolve().parents[2]


def engine():
    cfg = load_app_config(ROOT / "config/app.yaml").env
    cfg.slippage.liquidity_lambda = 0
    cfg.slippage.volatility_lambda = 0
    return ExecutionEngine(cfg)


def state(position="0", equity="100"):
    return ExecutionState(D(position), D(equity), D(0), D(0), D(100))


def test_overlimit_reduction_never_manufactures_liquidity():
    e = engine()
    market = MarketSnapshot(100, 0, 0, 1, 1)
    start = state("21", "100")
    result = e.apply_trade(start, D(0), market)
    assert result.delta_units == D("-0.01")
    assert abs(result.delta_units) * result.executed_price <= D(1)
    assert result.risk_reduction_only
    assert result.state.position == start.position + result.delta_units


def test_book_is_rewalked_after_postcost_margin_projection():
    e = engine()
    e._max_lev = D(1)
    book = LimitOrderBook((PriceLevel(D(99), D(20)),),
                          (PriceLevel(D(100), D(1)), PriceLevel(D(500), D(20))))
    result = e.apply_trade(state(equity="10"), D(10), MarketSnapshot(100,0,0,100000,100000,book))
    assert D(0) < result.delta_units < D("0.1")
    assert result.executed_price == D(100)
    assert result.state.position * result.executed_price <= result.state.equity


def test_zero_liquidity_is_zero_and_minimum_rechecked_after_projection():
    e = engine()
    assert e.apply_trade(state(), 1, MarketSnapshot(100,0,0,0,0)).delta_units == 0
    e._min_notional = D(50)
    result = e.apply_trade(state(equity="1"), 1, MarketSnapshot(100,0,0,100000,100000))
    assert result.delta_units == 0


def test_bid_price_must_not_understate_short_margin_exposure():
    e = engine()
    e._max_short_lev = D(1)
    e._short_cap = D(100)
    e._taker_fee = D(0)
    result = e.apply_trade(state(), D(-10), MarketSnapshot(100,200,0,100000,100000))
    assert result.delta_units < 0
    assert abs(result.state.position) * D(100) <= result.state.equity


def test_quote_debt_uses_marked_cash_and_entry_average_is_not_tick_rounded():
    e = engine()
    first = e.apply_trade(state(equity="1000"), D(15), MarketSnapshot(100,20,0,100000,100000))
    assert first.state.borrowed_quote == D(1500) - first.state.equity
    previous = first.state
    previous.equity += previous.position * D(10)
    second = e.apply_trade(previous, D(16), MarketSnapshot(110,20,0,100000,100000))
    expected = (D(15) * first.executed_price + second.delta_units * second.executed_price) / second.state.position
    assert second.state.average_entry_price == expected
    assert second.state.borrowed_quote == second.state.position * D(110) - second.state.equity


@settings(max_examples=100, deadline=None)
@given(target=st.floats(min_value=-5, max_value=50, allow_nan=False, allow_infinity=False),
       cap=st.floats(min_value=0.01, max_value=10000, allow_nan=False, allow_infinity=False),
       equity=st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False))
def test_all_final_constraints_hold_together(target, cap, equity):
    e = engine()
    before = state(equity=str(equity))
    result = e.apply_trade(before, target, MarketSnapshot(100, 10, 0, cap, cap))
    assert result.state.position == before.position + result.delta_units
    assert abs(result.delta_units) * result.executed_price <= D(str(cap))
    assert result.state.equity == before.equity - result.total_cost
    if result.delta_units:
        limit = e._max_lev if result.state.position >= 0 else e._max_short_lev
        assert abs(result.state.position) * result.executed_price <= result.state.equity * limit
        if result.state.position < 0:
            assert abs(result.state.position) <= e._short_cap
