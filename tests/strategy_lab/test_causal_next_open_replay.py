"""Next-open research replay: no same-close fills, fees, or broker authority."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_gym.strategy_lab.causal_replay import (
    replay_evaluated_signals,
    replay_strategy,
)
from trading_gym.strategy_lab.evaluator import evaluate_strategy
from trading_gym.strategy_lab.templates import template_catalogue

SOURCE = "a" * 64


def _frame(opens, closes, entries=(), exits=()):
    n = len(opens)
    opens = np.asarray(opens, dtype=float)
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes) + 1,
            "low": np.minimum(opens, closes) - 1,
            "close": closes,
            "volume": np.full(n, 1000.0),
            "entry_signal": [i in entries for i in range(n)],
            "exit_signal": [i in exits for i in range(n)],
        },
        index=pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
    )


def _replay(frame, **kwargs):
    return replay_evaluated_signals(frame, source_sha256=SOURCE, **kwargs)


def test_closed_bar_entry_only_fills_at_next_open():
    result = _replay(
        _frame([100, 100, 110], [120, 105, 110], entries=(0,)),
        allocation_fraction=0.5,
    )
    assert result.final_equity_base == pytest.approx(1.05)
    assert result.entry_count == 1 and result.exit_count == 0
    assert result.position_open is True
    assert result.execution_authority_granted is False


def test_last_bar_signal_is_not_filled_without_next_open():
    result = _replay(_frame([100, 100], [100, 101], entries=(1,)))
    assert result.final_equity_base == pytest.approx(1.0)
    assert result.trade_count == 0
    assert result.ignored_final_signal is True


def test_two_sided_fees_reduce_roundtrip_equity():
    bars = _frame(
        [100, 100, 110, 110], [100, 110, 110, 110],
        entries=(0,), exits=(1,),
    )
    free = _replay(bars, allocation_fraction=0.5, fee_bps=0)
    charged = _replay(bars, allocation_fraction=0.5, fee_bps=100)
    assert free.final_equity_base == pytest.approx(1.05)
    assert charged.final_equity_base < free.final_equity_base
    assert charged.entry_count == charged.exit_count == 1
    assert charged.transaction_cost_base > 0
    assert charged.position_open is False


def test_conflicting_signals_are_rejected_even_on_final_bar():
    bars = _frame([100, 100], [100, 100], entries=(1,), exits=(1,))
    with pytest.raises(ValueError, match="conflicting"):
        _replay(bars)


def test_duplicate_timestamps_do_not_replay():
    bars = _frame([100, 100, 102], [100, 100, 102], entries=(0,))
    stamps = list(bars.index)
    stamps[1] = stamps[0]
    bars.index = pd.DatetimeIndex(stamps)
    with pytest.raises(ValueError, match="timestamp"):
        _replay(bars)


def test_non_boolean_signal_is_not_accepted():
    bars = _frame([100, 100], [100, 101])
    bars["entry_signal"] = [0, 1]
    with pytest.raises(ValueError, match="boolean"):
        _replay(bars)


@pytest.mark.parametrize("bad", [True, -1, 1001, float("nan"), float("inf")])
def test_invalid_fees_fail_closed(bad):
    with pytest.raises(ValueError, match="fee_bps"):
        _replay(_frame([100, 100], [100, 100]), fee_bps=bad)


def test_hashes_change_when_underlying_market_evidence_changes():
    bars = _frame([100, 100, 105], [100, 101, 104], entries=(0,))
    a = _replay(bars)
    assert a == _replay(bars.copy(deep=True))
    bars.loc[bars.index[-1], "close"] = 105
    b = _replay(bars)
    assert a.input_sha256 != b.input_sha256
    assert a.report_sha256 != b.report_sha256
    assert a.mutation_performed is False
    assert a.venue_fills_verified is False
    assert a.stop_loss_simulated is False


def test_final_bar_exit_remains_unfilled():
    result = _replay(
        _frame([100, 100, 100], [100, 100, 100],
               entries=(0,), exits=(2,)),
        fee_bps=10,
    )
    assert result.entry_count == 1 and result.exit_count == 0
    assert result.ignored_final_signal is True


def test_input_frame_is_not_modified():
    bars = _frame([100, 100, 102], [100, 100, 102], entries=(0,))
    before = bars.copy(deep=True)
    _replay(bars)
    pd.testing.assert_frame_equal(before, bars)


def test_replay_strategy_uses_validated_declarative_risk_fraction():
    strategy = template_catalogue()["rsi_mean_reversion"]
    prices = np.linspace(120, 100, 80)
    bars = _frame(prices, prices).drop(columns=["entry_signal", "exit_signal"])
    result = replay_strategy(bars, strategy, source_sha256=SOURCE, fee_bps=5)
    expected = replay_evaluated_signals(
        evaluate_strategy(bars, strategy),
        source_sha256=SOURCE,
        allocation_fraction=strategy.risk.max_position_fraction,
        fee_bps=5,
    )
    assert result == expected
    assert result.execution_authority_granted is False
