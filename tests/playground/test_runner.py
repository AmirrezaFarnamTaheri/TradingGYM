"""AXIOM-1 playground runner (ADR-031): deterministic strategy simulation.

Contract:
- PlaygroundRun = f(strategy, window, overlays, cost params, seed) →
  deterministic result. Same inputs → byte-identical equity curve.
- No lookahead: the decision at bar t uses data ≤ t; every fill happens
  at bar t+1's open. Machine-checked.
- Long-only v1: entry_signal opens, exit_signal closes; intent -1 and 0
  both mean flat (the strategy grammar emits 1/-1 intent; -1 closes).
- Costs: per-side fee + half-spread charged on every position change.
- The result carries a manifest (sha256 of window, strategy, overlays)
  so runs are forensically reproducible.
- Bounded: window bars above the limit fail fast at construction.
"""

from __future__ import annotations


import numpy as np
import pandas as pd
import pytest

from trading_gym.agent.playground.runner import (
    PlaygroundRunSpec,
    run_playground,
)
from trading_gym.agent.playground.scenarios import Scenario
from trading_gym.agent.strategy_lab.models import (
    Condition,
    ConditionGroup,
    IndicatorSpec,
    Operand,
    Operator,
    StrategySpec,
)


def make_frame(n: int = 240, seed: int = 11, spread_bps: float = 2.0) -> pd.DataFrame:
    if n == 0:
        return pd.DataFrame(
            {"open": [], "high": [], "low": [], "close": [], "volume": []},
            index=pd.DatetimeIndex([], tz="UTC"),
        )
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-07-01T00:00:00Z", periods=n, freq="5min", tz="UTC")
    close = 50_000 * np.exp(np.cumsum(rng.normal(0.0002, 0.0015, n)))
    open_ = np.roll(close, 1)
    open_[0] = 50_000.0
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0004, n)))
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(10, 1000, n),
            "spread_bps": np.full(n, spread_bps),
        },
        index=index,
    )


def momentum_strategy() -> StrategySpec:
    """SMA(3/10) cross: enter on fast crossing above slow, exit below."""

    return StrategySpec(
        name="sma-cross",
        description="playground fixture",
        timeframe="5m",
        indicators=[
            IndicatorSpec(alias="sma_fast", name="sma", params={"period": 3}),
            IndicatorSpec(alias="sma_slow", name="sma", params={"period": 10}),
        ],
        entry=ConditionGroup(
            mode="all",
            conditions=[
                Condition(
                    left=Operand(kind="indicator", value="sma_fast"),
                    operator=Operator.CROSSES_ABOVE,
                    right=Operand(kind="indicator", value="sma_slow"),
                )
            ],
        ),
        exit=ConditionGroup(
            mode="all",
            conditions=[
                Condition(
                    left=Operand(kind="indicator", value="sma_fast"),
                    operator=Operator.CROSSES_BELOW,
                    right=Operand(kind="indicator", value="sma_slow"),
                )
            ],
        ),
    )


def make_spec(**overrides: object) -> PlaygroundRunSpec:
    base: dict[str, object] = {
        "window": make_frame(),
        "strategy": momentum_strategy(),
        "overlays": (),
        "initial_capital": 10_000.0,
        "fee_bps": 10.0,
        "position_fraction": 1.0,
    }
    base.update(overrides)
    return PlaygroundRunSpec(**base)  # type: ignore[arg-type]


class TestRunnerBasics:
    def test_run_produces_deterministic_result(self) -> None:
        first = run_playground(make_spec())
        second = run_playground(make_spec())
        pd.testing.assert_frame_equal(first.equity_curve, second.equity_curve)
        assert first.metrics == second.metrics
        assert first.manifest == second.manifest

    def test_metrics_are_self_consistent(self) -> None:
        result = run_playground(make_spec())
        equity = result.equity_curve["equity"]
        assert result.metrics["n_bars"] == len(equity)
        assert result.metrics["final_equity"] == pytest.approx(equity.iloc[-1])
        assert 0.0 <= result.metrics["win_rate"] <= 1.0
        assert result.metrics["max_drawdown"] >= 0.0
        # equity never goes negative under long-only with bounded costs
        assert (equity > 0).all()

    def test_manifest_pins_inputs(self) -> None:
        result = run_playground(make_spec())
        manifest = result.manifest
        assert manifest["window_sha256"]
        assert manifest["strategy_name"] == "sma-cross"
        assert manifest["overlays"] == ()
        assert len(manifest["window_sha256"]) == 64

    def test_window_bar_limit_enforced(self) -> None:
        with pytest.raises(ValueError):
            run_playground(make_spec(window=make_frame(2_000_001)))

    def test_empty_frame_rejected(self) -> None:
        with pytest.raises(ValueError):
            run_playground(make_spec(window=make_frame(0)))


class TestNoLookahead:
    def test_fill_happens_after_signal(self) -> None:
        result = run_playground(make_spec())
        # position changes must lag the signal: rebuild signals and compare
        spec = make_spec()
        from trading_gym.agent.strategy_lab.evaluator import evaluate_strategy

        signals = evaluate_strategy(spec.window, spec.strategy)
        entries = signals.index[signals["entry_signal"]]
        fills = [ts for ts, change in result.positions_change_points() if change == 1]
        assert fills, "fixture strategy must produce at least one entry"
        for fill_ts in fills:
            prior = entries[entries < fill_ts]
            assert len(prior) > 0, "entry fill cannot precede or coincide with signal"

    def test_equity_unchanged_before_first_fill(self) -> None:
        result = run_playground(make_spec())
        changes = result.positions_change_points()
        if not changes:
            return
        first_ts = changes[0][0]
        idx = result.equity_curve.index.get_loc(first_ts)
        initial = result.equity_curve["equity"].iloc[0]
        # cash is untouched before the first fill — bit-exact, no approx
        assert (result.equity_curve["equity"].iloc[:idx] == initial).all()


class TestCosts:
    def test_zero_fee_zero_spread_means_zero_costs(self) -> None:
        result = run_playground(
            make_spec(fee_bps=0.0, window=make_frame(spread_bps=0.0))
        )
        assert all(trade["cost_quote"] == pytest.approx(0.0) for trade in result.trades)

    def test_half_spread_charged_with_zero_fee(self) -> None:
        # spread 2 bps → half-spread 1 bp per side on each trade's notional
        result = run_playground(make_spec(fee_bps=0.0))
        for trade in result.trades:
            assert trade["cost_quote"] > 0.0

    def test_fee_reduces_final_equity(self) -> None:
        free = run_playground(make_spec(fee_bps=0.0)).metrics["final_equity"]
        paid = run_playground(make_spec(fee_bps=25.0)).metrics["final_equity"]
        assert paid < free

    def test_position_fraction_scales_exposure(self) -> None:
        full = run_playground(make_spec(position_fraction=1.0))
        half = run_playground(make_spec(position_fraction=0.5))
        assert (
            half.equity_curve["equity"].std()
            <= full.equity_curve["equity"].std() + 1e-9
        )


class TestOverlayIntegration:
    def test_overlay_changes_result(self) -> None:
        plain = run_playground(make_spec())
        scenario = Scenario.parse(
            {
                "scenario_id": "crash",
                "window": {
                    "symbol": "BTCUSDT",
                    "timeframe": "5m",
                    "frame": make_frame(),
                },
                "overlays": [
                    {
                        "type": "gap_injection",
                        "params": {
                            "kind": "flash_crash",
                            "magnitude_pct": -15.0,
                            "recovery_bars": 10,
                            "start": 100,
                        },
                    }
                ],
                "seed": 5,
                "repetitions": 1,
            }
        )
        overlays = scenario.overlays
        stressed = run_playground(
            make_spec(window=scenario.stressed_frame(), overlays=overlays)
        )
        assert not stressed.metrics["final_equity"] == pytest.approx(
            plain.metrics["final_equity"]
        )
        assert stressed.manifest["overlays"] == (
            {
                "type": "gap_injection",
                "params": {
                    "kind": "flash_crash",
                    "magnitude_pct": -15.0,
                    "recovery_bars": 10,
                    "start": 100,
                },
            },
        )

    def test_stale_feed_reduces_trade_count(self) -> None:
        plain = run_playground(make_spec())
        scenario = Scenario.parse(
            {
                "scenario_id": "frozen",
                "window": {
                    "symbol": "BTCUSDT",
                    "timeframe": "5m",
                    "frame": make_frame(),
                },
                "overlays": [
                    {
                        "type": "stale_feed",
                        "params": {"duration_bars": 30, "start": 60},
                    }
                ],
                "seed": 5,
                "repetitions": 1,
            }
        )
        frozen = run_playground(make_spec(window=scenario.stressed_frame()))
        assert frozen.metrics["n_trades"] <= plain.metrics["n_trades"]


class TestResultType:
    def test_positions_change_points_pairs_index_and_direction(self) -> None:
        result = run_playground(make_spec())
        for ts, change in result.positions_change_points():
            assert change in (1, -1)
            assert ts in result.equity_curve.index

    def test_trades_have_complete_contract(self) -> None:
        result = run_playground(make_spec())
        for trade in result.trades:
            assert {"entry_ts", "exit_ts", "pnl_quote", "cost_quote"} <= set(trade)
            assert trade["pnl_quote"] is not None


class TestPlaygroundInputIntegrity:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("initial_capital", float("nan")),
            ("initial_capital", float("inf")),
            ("fee_bps", float("nan")),
            ("fee_bps", float("inf")),
            ("fee_bps", 10000.0),
            ("position_fraction", float("nan")),
            ("position_fraction", float("inf")),
        ],
    )
    def test_nonfinite_or_impossible_costs_rejected_at_admission(self, field, value):
        with pytest.raises(ValueError):
            make_spec(**{field: value})

    @pytest.mark.parametrize("spread", [float("nan"), float("inf"), -0.01, 20000.0])
    def test_nonfinite_negative_or_unaffordable_spreads_rejected(self, spread):
        frame = make_frame()
        frame.loc[frame.index[70], "spread_bps"] = spread
        with pytest.raises(ValueError):
            make_spec(window=frame)

    def test_combined_fee_and_spread_cannot_produce_negative_units(self):
        with pytest.raises(ValueError):
            make_spec(fee_bps=9999.0, window=make_frame(spread_bps=4.0))

    @pytest.mark.parametrize("price", [0.0, -5.0])
    def test_zero_or_negative_market_prices_rejected(self, price):
        frame = make_frame()
        frame.loc[frame.index[15], ["open", "high", "low", "close"]] = price
        with pytest.raises(ValueError):
            make_spec(window=frame)

    def test_duplicate_or_reversed_timestamp_rejected(self):
        frame = make_frame()
        for index in (frame.index.insert(1, frame.index[0])[:-1], frame.index[::-1]):
            candidate = frame.copy()
            candidate.index = index
            with pytest.raises(ValueError):
                make_spec(window=candidate)
