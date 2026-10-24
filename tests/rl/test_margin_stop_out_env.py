"""Env-level runtime verification of the graded stop-out cascade.

The planner (``tests/margin/test_stop_out_cascade.py``) is unit-tested in
isolation, but no test drove ``MarginTradingEnv.step()`` end-to-end into a
margin shortfall.  These tests build a real leveraged book through the
execution engine, crash the tape into a shortfall, and assert the live
behaviour of ``_handle_margin_shortfall``:

- **Graded cascade** (``stop_out_cascade=True``, the default): closes the
  worst unrealised-P&L lot first, trims to the minimal restoring fraction,
  restores the borrow-based margin ratio above the liquidation threshold,
  keeps the episode alive, and preserves the lot-ledger / engine twin
  invariant.  Equity conservation is checked against the marked-equity
  identity the cascade's own docstring states (``entry-to-mark already
  absorbed by earlier mark-to-market steps``).
- **Terminal fallback** (``stop_out_cascade=False``, the legacy all-or-nothing
  path, and the empty-lot externally-constructed-state path): forced
  liquidation realises the remaining move from the current mid (the marking
  reference) to the exec price — *not* from the previous bar's mid, which the
  continuous mark-to-market at the top of ``step()`` has already booked.

The terminal-accounting tests pin the corrected vs-mark booking; they are the
regression for the donor-port double-book where ``pos·(exec − prev_mid)`` was
booked on top of an already-applied ``pos·(mid − prev_mid)`` mark-to-market.

The circuit breaker is neutralised here to isolate stop-out accounting.
Actions execute at completed row t and settle through the next row; future
OHLC envelopes therefore belong to the action that held inventory into them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_gym.common.config import (
    AttentionConfig,
    BreakerConfig,
    CapacityConfig,
    CostConfig,
    EnvConfig,
    FeatureConfig,
    FinancingConfig,
    LeverageConfig,
    LiquidityConfig,
    RewardConfig,
    ShortsConfig,
    SlippageConfig,
    StandardizationConfig,
    TimeframeConfig,
    VenueDecimals,
)
from trading_gym.rl.margin_trading_environment import MarginTradingEnv

# ---------------------------------------------------------------------------
# Fixtures — a clean-accounting config so the stop-out mechanics are isolated
# from microstructure noise.  Near-zero spread/slippage/fees make the marked-
# equity identities exact and falsifiable.
# ---------------------------------------------------------------------------


def _env_config(*, stop_out_cascade: bool = True) -> EnvConfig:
    return EnvConfig(
        symbol="BTCUSDT",
        quote_ccy="USDT",
        decimals=VenueDecimals(price={"BTCUSDT": 2}, qty={"BTCUSDT": 8}),
        leverage=LeverageConfig(
            min_leverage=1.0,
            max_leverage=20.0,
            max_short_leverage=5.0,
            warning_margin_ratio=0.10,
            liquidation_margin_ratio=0.03,
            financing=FinancingConfig(daily_pct=0.0, periods_per_day=288),
            stop_out_cascade=stop_out_cascade,
        ),
        shorts=ShortsConfig(
            enabled=True, allowed_symbols=["BTCUSDT"], borrow_caps={"BTCUSDT": 10.0}
        ),
        costs=CostConfig(
            taker_fee_bps=1.0, dead_zone_equity_frac=0.0001, max_spread_bps=50.0
        ),
        slippage=SlippageConfig(
            volatility_lambda=0.0,
            liquidity_lambda=0.0,
            stress_multiplier=1.0,
            volume_reference_fraction=0.01,
        ),
        liquidity=LiquidityConfig(default_quote_cap=1_000_000.0),
        capacity=CapacityConfig(conservative_default=10_000_000.0),
        breaker=BreakerConfig(
            max_loss_pct=1e9,
            max_loss_velocity_pct_per_hour=1e9,
            max_single_trade_loss_pct=1e9,
            margin_check_enabled=False,
            stall_check_enabled=False,
        ),
        rewards=RewardConfig(
            scaling=1.0, death_penalty=-100.0, clip_min=-10.0, clip_max=10.0
        ),
    )


def _feature_config() -> FeatureConfig:
    return FeatureConfig(
        timeframes=[TimeframeConfig(name="5m", bar_minutes=5, roll_fill_cap=12)],
        standardization=StandardizationConfig(method="robust", fit_window_bars=100),
        attention=AttentionConfig(enabled=False),
        rolling_window=200,
    )


def _tape(prices: dict[int, tuple[float, float, float]]) -> dict:
    """Build a T=300 tape flat outside ``prices``.

    ``prices`` maps bar index -> (mid, high, low).  With T=300 and
    ``rolling_window=200`` the env resets deterministically to ``t=200``.
    """
    T = 300
    mid = np.full(T, 100.0, dtype=np.float64)
    high = np.full(T, 100.5, dtype=np.float64)
    low = np.full(T, 99.5, dtype=np.float64)
    for bar, (m, h, lo) in prices.items():
        mid[bar] = m
        high[bar] = h
        low[bar] = lo
    cols = ["log_ret", "volatility", "rsi", "spread_ratio", "sector", "can_short"]
    feat = np.zeros((T, len(cols)), dtype=np.float32)
    feat[:, 3] = 0.0001  # 1 bps spread — under the 50 bps skip ceiling
    return {
        "features": pd.DataFrame(feat, columns=cols),
        "prices_mid": mid,
        "prices_high": high,
        "prices_low": low,
    }


def _build_env(
    tape: dict, *, stop_out_cascade: bool = True, initial_equity: float = 1000.0
) -> MarginTradingEnv:
    return MarginTradingEnv(
        features=tape["features"],
        prices_mid=tape["prices_mid"],
        prices_high=tape["prices_high"],
        prices_low=tape["prices_low"],
        cfg=_env_config(stop_out_cascade=stop_out_cascade),
        features_cfg=_feature_config(),
        initial_equity=initial_equity,
        training_stats={"mean": 0.0, "std": 1.0},
    )


# ---------------------------------------------------------------------------
# Graded cascade through step()
# ---------------------------------------------------------------------------


def test_graded_cascade_fires_through_step_and_restores_margin():
    """A multi-lot long book, dipped into a shortfall, must grade: close the
    worst lot first, trim to the minimal restoring fraction, survive, and keep
    the lot-ledger twin equal to the engine net position.
    """
    dip_bar, recovery_bar = 204, 205
    tape = _tape(
        {
            200: (100.0, 100.5, 99.5),  # entry bar: action 10x -> lot 1
            201: (100.0, 100.5, 99.5),  # add: action 15x        -> lot 2
            202: (100.0, 100.5, 99.5),  # add: action 20x        -> lot 3
            dip_bar: (99.0, 99.5, 97.0),  # mid 99, low 97 -> shortfall
            recovery_bar: (100.0, 100.5, 99.5),  # episode must still be alive
        }
    )
    env = _build_env(tape)
    env.reset(seed=0)

    # Accumulate three lots at identical entries (tie-break by lot id).
    env.step(np.array([10.0], dtype=np.float32))
    env.step(np.array([15.0], dtype=np.float32))
    env.step(np.array([20.0], dtype=np.float32))

    pre_step_equity = float(env._state.equity)
    pre_step_position = float(env._state.position)
    pre_step_borrow = float(env._state.borrowed_quote)
    pre_step_open_lots = env._lots.open_lots()
    prev_mid = env.prev_mid_price
    assert pre_step_position > 0
    assert len(pre_step_open_lots) == 3, "book must hold one lot per accumulation step"

    # Hold existing units into the next bar; its adverse envelope must fire the cascade.
    hold_leverage = float(env.position) * env.price[env.t] / float(env.equity)
    _, _, terminated, truncated, info = env.step(hold_leverage)

    # --- Cascade fired, episode survived ---
    assert info["graded_stop_out"] is True
    assert info["reason"] == "stop_out_cascade"
    assert info["liquidation"] is False
    assert terminated is False
    assert env.done is False
    assert len(info["stop_out_events"]) >= 1, "cascade must close at least one slice"

    # --- Worst-first: with identical entries the tie-break is lowest lot id ---
    closed_lot_ids = [ev["lot_id"] for ev in info["stop_out_events"]]
    worst_lot = max(pre_step_open_lots, key=lambda lot: (lot.entry_price, -lot.lot_id))
    assert (
        closed_lot_ids[0] == worst_lot.lot_id
    ), "cascade must close the worst lot first (entry ties break by id)"

    # --- Trim: the first closure is a partial close, not the whole lot ---
    first_event = info["stop_out_events"][0]
    first_lot = next(
        lot for lot in pre_step_open_lots if lot.lot_id == closed_lot_ids[0]
    )
    assert (
        0 < first_event["quantity"] < float(first_lot.quantity)
    ), "minimal restoring fraction must trim, not dump the whole lot"

    # --- Book survives: position reduced but non-zero, borrow de-risked ---
    assert 0 < float(env._state.position) < pre_step_position
    assert float(env._state.borrowed_quote) < pre_step_borrow
    assert env._lots.open_lots(), "lot book must retain open slices"

    # --- Lot-ledger twin invariant survives the cascade + engine trade ---
    assert float(env._lots.net_position()) == pytest.approx(
        float(env._state.position), abs=1e-9
    ), "lot twin must equal engine net position after every step"

    # --- Marked-equity identity: equity_after = equity_before + MTM + closures
    #                                                                     − costs − financing
    mid = tape["prices_mid"][dip_bar]
    mtm_delta = pre_step_position * (mid - prev_mid)
    closure_sum = sum(ev["equity_delta"] for ev in info["stop_out_events"])
    costs = info["crossing_cost"] + info["slippage_cost"] + info["fee_cost"]
    expected_equity = (
        pre_step_equity + mtm_delta + closure_sum - costs - info["financing_paid"]
    )
    assert float(env._state.equity) == pytest.approx(
        expected_equity, abs=1e-6
    ), "equity must conserve: marked-to-mid + cascade closures − engine costs − financing"

    # --- Episode still steppable on the next (recovery) bar ---
    env.step(np.array([5.0], dtype=np.float32))
    assert env.done is False, "episode must continue after a graded stop-out"


# ---------------------------------------------------------------------------
# Terminal fallback accounting — the donor-port double-book regression
# ---------------------------------------------------------------------------


def test_terminal_forced_liquidation_books_vs_mark_not_vs_prev_mid():
    """Legacy terminal path (``stop_out_cascade=False``) must realise the
    remaining move from the current mid (the marking reference) to the exec
    price, because ``_apply_mark_to_market`` already booked ``pos·(mid −
    prev_mid)`` at the top of ``step()``.

    A gap-down bar (mid == low == exec) isolates the accounting: the step's
    total realised move is ``pos·(exec − prev_mid)``, of which the MTM has
    already booked the whole slice (mid == exec), so the terminal booking must
    add exactly zero.  The legacy donor form ``pos·(exec − prev_mid)`` booked
    on top of the MTM double-books the entire bar move.  The gap is sized so
    the shortfall fires while marked equity stays positive — a deeper gap
    trips the breaker's invalid-context guard (negative nav) before the
    liquidation path is reached.
    """
    gap_bar = 202
    gap_price = 96.5
    tape = _tape(
        {
            200: (100.0, 100.5, 99.5),  # entry: action 20x builds the book
            gap_bar: (gap_price, gap_price, gap_price),  # gap-down: mid == low == exec
        }
    )
    env = _build_env(tape, stop_out_cascade=False)
    env.reset(seed=0)

    env.step(np.array([20.0], dtype=np.float32))  # build 20x long at ~100
    pre_step_equity = float(env._state.equity)
    pre_step_position = float(env._state.position)
    prev_mid = env.prev_mid_price
    assert pre_step_position > 0

    hold_leverage = float(env.position) * env.price[env.t] / float(env.equity)
    _, _, terminated, truncated, info = env.step(hold_leverage)

    # --- Terminal forced liquidation ---
    assert info["liquidation"] is True
    assert info["reason"] == "forced_liquidation"
    assert info["graded_stop_out"] is False
    assert terminated is True
    assert env.done is True

    # --- Book fully flattened ---
    assert float(env._state.position) == 0
    assert float(env._state.borrowed_base) == 0
    assert float(env._state.borrowed_quote) == 0
    assert env._lots.open_lots() == ()

    # --- Marked-equity identity (the regression): the MTM at the top of
    # step() already booked pos·(mid − prev_mid); with a gap bar mid == low ==
    # exec the terminal booking must add zero, leaving equity_after == eq_before
    # + pos·(exec − prev_mid).  The legacy form double-books the whole move.
    exec_price = info["liquidation_price"]
    expected_equity = pre_step_equity + pre_step_position * (exec_price - prev_mid)
    assert float(env._state.equity) == pytest.approx(expected_equity, abs=1e-6), (
        "terminal liquidation must book the move from the current mid (marking "
        "reference) to exec, not re-book the already-applied mid-to-prev_mid slice"
    )


def test_terminal_path_with_cascade_disabled_uses_intra_bar_exec_price():
    """A wick bar (low < mid) exercises the intra-bar terminal booking: the
    correct addition is ``pos·(low − mid)`` (the slice from the already-marked
    mid down to the exec/low), *not* ``pos·(low − prev_mid)``.
    """
    wick_bar = 202
    tape = _tape(
        {
            200: (100.0, 100.5, 99.5),
            wick_bar: (95.0, 95.5, 90.0),  # mid 95, low 90 -> intra-bar terminal
        }
    )
    env = _build_env(tape, stop_out_cascade=False)
    env.reset(seed=0)
    env.step(np.array([20.0], dtype=np.float32))

    pre_step_equity = float(env._state.equity)
    pre_step_position = float(env._state.position)
    prev_mid = env.prev_mid_price

    hold_leverage = float(env.position) * env.price[env.t] / float(env.equity)
    _, _, terminated, _, info = env.step(hold_leverage)

    assert terminated is True
    assert info["liquidation"] is True
    exec_price = info["liquidation_price"]
    mid = tape["prices_mid"][wick_bar]
    # MTM booked pos·(mid − prev_mid); terminal must add pos·(exec − mid).
    expected_equity = (
        pre_step_equity
        + pre_step_position * (mid - prev_mid)
        + pre_step_position * (exec_price - mid)
    )
    assert float(env._state.equity) == pytest.approx(expected_equity, abs=1e-6), (
        "intra-bar terminal must book pos·(exec − mid) on top of the MTM, "
        "not pos·(exec − prev_mid)"
    )
