"""Rust kernel ↔ Python reference parity (ADR-035).

The kernel is an optimisation, so it earns no new semantics: this suite
asserts the accelerated path reproduces the reference *bit for bit* —
equity curves compared with ``array_equal`` on raw doubles, trade
ledgers compared field by field — over randomised windows, stressed
overlays, both cost regimes, and the force-close edge case.

When no compiled artifact exists the parity tests skip (the reference is
then the only implementation and there is nothing to compare), while the
loader contract tests still run: an absent kernel must degrade, never
raise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_gym.playground import kernel as kernel_module
from trading_gym.playground.runner import (
    PlaygroundRunSpec,
    _run_kernel,
    _run_reference,
    run_playground,
)
from trading_gym.strategy_lab.evaluator import evaluate_strategy
from trading_gym.strategy_lab.models import (
    Condition,
    ConditionGroup,
    IndicatorSpec,
    Operand,
    Operator,
    StrategySpec,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def frame(n: int = 300, seed: int = 5, spread_bps: float = 2.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-03-01T00:00:00Z", periods=n, freq="5min", tz="UTC")
    close = 30_000 * np.exp(np.cumsum(rng.normal(0.0001, 0.0018, n)))
    open_ = np.roll(close, 1)
    open_[0] = 30_000.0
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0005, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0005, n)))
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(5, 900, n),
            "spread_bps": np.full(n, spread_bps),
        },
        index=index,
    )


def cross_strategy(fast: int = 4, slow: int = 12) -> StrategySpec:
    return StrategySpec(
        name="kernel-parity-cross",
        timeframe="5m",
        indicators=[
            IndicatorSpec(alias="fast", name="sma", params={"period": fast}),
            IndicatorSpec(alias="slow", name="sma", params={"period": slow}),
        ],
        entry=ConditionGroup(
            mode="all",
            conditions=[
                Condition(
                    left=Operand(kind="indicator", value="fast"),
                    operator=Operator.CROSSES_ABOVE,
                    right=Operand(kind="indicator", value="slow"),
                )
            ],
        ),
        exit=ConditionGroup(
            mode="all",
            conditions=[
                Condition(
                    left=Operand(kind="indicator", value="fast"),
                    operator=Operator.CROSSES_BELOW,
                    right=Operand(kind="indicator", value="slow"),
                )
            ],
        ),
    )


def _arrays(spec: PlaygroundRunSpec):
    window = spec.window
    signals = evaluate_strategy(window, spec.strategy)
    return {
        "intent": signals["position_intent"].to_numpy(dtype=np.int8),
        "open_": window["open"].to_numpy(dtype=np.float64),
        "close": window["close"].to_numpy(dtype=np.float64),
        "spread": (
            window["spread_bps"].to_numpy(dtype=np.float64)
            if "spread_bps" in window.columns
            else np.zeros(len(window))
        ),
        "index": window.index,
    }


def _assert_bit_identical(spec: PlaygroundRunSpec) -> None:
    arrays = _arrays(spec)
    accelerated = _run_kernel(spec, **arrays)
    assert accelerated is not None, "kernel reported available but returned None"
    reference = _run_reference(spec, **arrays)

    fast_equity, fast_trades, fast_changes, fast_bars = accelerated
    ref_equity, ref_trades, ref_changes, ref_bars = reference

    assert np.array_equal(fast_equity, ref_equity), "equity curves differ bitwise"
    assert fast_bars == ref_bars
    assert len(fast_trades) == len(ref_trades)
    for fast_trade, ref_trade in zip(fast_trades, ref_trades):
        assert fast_trade["entry_ts"] == ref_trade["entry_ts"]
        assert fast_trade["exit_ts"] == ref_trade["exit_ts"]
        for field in ("entry_price", "exit_price", "pnl_quote", "cost_quote"):
            assert (
                fast_trade[field] == ref_trade[field]
            ), f"{field} differs: {fast_trade[field]!r} != {ref_trade[field]!r}"
    assert list(fast_changes) == list(ref_changes)


requires_kernel = pytest.mark.skipif(
    not kernel_module.kernel_available(),
    reason="no compiled axiom_kernel artifact (build rust/axiom_kernel)",
)


class TestLoaderContract:
    def test_status_reports_availability_and_reason(self) -> None:
        status = kernel_module.kernel_status()
        assert set(status) == {"available", "abi_version", "detail"}
        assert isinstance(status["available"], bool)
        assert status["abi_version"] == 1
        assert status["detail"]

    def test_absent_kernel_degrades_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(kernel_module, "load_kernel", lambda: None)
        result = kernel_module.simulate(
            intent=np.array([1, 0, -1], dtype=np.int8),
            open_=np.array([1.0, 1.0, 1.0]),
            close=np.array([1.0, 1.0, 1.0]),
            spread_bps=np.zeros(3),
            initial_capital=100.0,
            fee_bps=0.0,
            position_fraction=1.0,
        )
        assert result is None

    def test_runner_result_is_independent_of_the_kernel(self) -> None:
        """The public result must not reveal which loop ran."""

        spec = PlaygroundRunSpec(
            window=frame(180, seed=17),
            strategy=cross_strategy(),
            initial_capital=10_000.0,
            fee_bps=10.0,
        )
        arrays = _arrays(spec)
        reference = _run_reference(spec, **arrays)
        result = run_playground(spec)
        assert np.array_equal(
            result.equity_curve["equity"].to_numpy(dtype=np.float64),
            reference[0],
        )
        assert result.metrics["n_trades"] == len(reference[1])


@requires_kernel
class TestKernelParity:
    @pytest.mark.parametrize("seed", [3, 11, 29, 101])
    def test_random_windows_match_bitwise(self, seed: int) -> None:
        _assert_bit_identical(
            PlaygroundRunSpec(
                window=frame(260, seed=seed),
                strategy=cross_strategy(),
                initial_capital=10_000.0,
                fee_bps=10.0,
            )
        )

    def test_zero_cost_regime_matches_bitwise(self) -> None:
        _assert_bit_identical(
            PlaygroundRunSpec(
                window=frame(200, seed=7, spread_bps=0.0),
                strategy=cross_strategy(),
                initial_capital=5_000.0,
                fee_bps=0.0,
            )
        )

    def test_partial_position_fraction_matches_bitwise(self) -> None:
        _assert_bit_identical(
            PlaygroundRunSpec(
                window=frame(220, seed=23),
                strategy=cross_strategy(3, 8),
                initial_capital=25_000.0,
                fee_bps=7.5,
                position_fraction=0.35,
            )
        )

    def test_force_close_at_window_end_matches_bitwise(self) -> None:
        """A strategy that never exits leaves the mark-to-close path."""

        never_exit = StrategySpec(
            name="kernel-parity-hold",
            timeframe="5m",
            indicators=[
                IndicatorSpec(alias="fast", name="sma", params={"period": 3}),
                IndicatorSpec(alias="slow", name="sma", params={"period": 9}),
            ],
            entry=ConditionGroup(
                mode="all",
                conditions=[
                    Condition(
                        left=Operand(kind="indicator", value="fast"),
                        operator=Operator.CROSSES_ABOVE,
                        right=Operand(kind="indicator", value="slow"),
                    )
                ],
            ),
            exit=ConditionGroup(
                mode="all",
                conditions=[
                    Condition(
                        left=Operand(kind="constant", value=1.0),
                        operator=Operator.LT,
                        right=Operand(kind="constant", value=0.0),
                    )
                ],
            ),
        )
        _assert_bit_identical(
            PlaygroundRunSpec(
                window=frame(150, seed=41),
                strategy=never_exit,
                initial_capital=10_000.0,
                fee_bps=10.0,
            )
        )

    def test_kernel_rejects_invalid_inputs_instead_of_guessing(self) -> None:
        with pytest.raises(kernel_module.KernelError):
            kernel_module.simulate(
                intent=np.array([1, 0, -1], dtype=np.int8),
                open_=np.array([1.0, 1.0, 1.0]),
                close=np.array([1.0, 1.0, 1.0]),
                spread_bps=np.zeros(3),
                initial_capital=-5.0,  # invalid
                fee_bps=0.0,
                position_fraction=1.0,
            )

    def test_kernel_rejects_non_finite_prices(self) -> None:
        with pytest.raises(kernel_module.KernelError):
            kernel_module.simulate(
                intent=np.array([1, 0, -1], dtype=np.int8),
                open_=np.array([1.0, float("nan"), 1.0]),
                close=np.array([1.0, 1.0, 1.0]),
                spread_bps=np.zeros(3),
                initial_capital=100.0,
                fee_bps=0.0,
                position_fraction=1.0,
            )
