"""AXIOM-1 Monte-Carlo runner + overlay attribution (ADR-031 §1).

Contract:
- ``run_monte_carlo`` executes the base window once plus N seeded
  repetitions through ``run_playground``; per-repetition results are
  reduced to a distribution over ``final_return_pct`` with mean/std/
  percentiles and a worst-case drawdown envelope.
- ``attribute_overlays`` isolates each overlay's marginal effect: base
  final equity minus the single-overlay-applied equity, in overlay
  declaration order, with the interaction term as the residual.
- Overlay attribution is additive-checked: single-overlay effects plus
  interaction equal the full-overlay result (machine-checked in tests).
- Determinism: same scenario → same MC table (seeded generators flow
  through ``Scenario.stressed_frames``).
- Returns arrays are finite or the run fails (a NaN equity curve is a
  bug, not a metric).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from trading_gym.playground.attribution import (
    AttributionBreakdown,
    MonteCarloReport,
    attribute_overlays,
    run_monte_carlo,
)
from trading_gym.playground.runner import PlaygroundRunSpec
from trading_gym.playground.scenarios import Scenario
from trading_gym.strategy_lab.models import (
    Condition,
    ConditionGroup,
    IndicatorSpec,
    Operand,
    Operator,
    StrategySpec,
)


def make_frame(n: int = 240, seed: int = 11, spread_bps: float = 2.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-07-01", periods=n, freq="5min", tz="UTC")
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
    return StrategySpec(
        name="sma-cross",
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
        "initial_capital": 10_000.0,
        "fee_bps": 10.0,
    }
    base.update(overrides)
    return PlaygroundRunSpec(**base)  # type: ignore[arg-type]


def scenario_with_overlays(overlays: list[dict], n: int = 240) -> Scenario:
    return Scenario.parse(
        {
            "scenario_id": "mc-test",
            "window": {"symbol": "BTCUSDT", "timeframe": "5m", "frame": make_frame(n)},
            "overlays": overlays,
            "seed": 20260901,
            "repetitions": 5,
        }
    )


class TestMonteCarlo:
    def test_report_shape_and_reductions(self) -> None:
        scenario = scenario_with_overlays(
            [
                {
                    "type": "gap_injection",
                    "params": {
                        "kind": "flash_crash",
                        "magnitude_pct": -8.0,
                        "recovery_bars": 5,
                        "start": 100,
                        "randomize": ["magnitude_pct"],
                        "ranges": {"magnitude_pct": [-12.0, -4.0]},
                    },
                }
            ]
        )
        report = run_monte_carlo(make_spec(), scenario)
        assert isinstance(report, MonteCarloReport)
        assert report.n_repetitions == 5
        assert math.isfinite(report.mean_return_pct)
        assert math.isfinite(report.std_return_pct)
        assert report.percentile_5 <= report.median <= report.percentile_95
        assert 0.0 <= report.worst_drawdown <= 1.0

    def test_deterministic_across_calls(self) -> None:
        scenario = scenario_with_overlays(
            [
                {
                    "type": "gap_injection",
                    "params": {
                        "kind": "flash_crash",
                        "magnitude_pct": -8.0,
                        "recovery_bars": 5,
                        "start": 100,
                    },
                }
            ]
        )
        first = run_monte_carlo(make_spec(), scenario)
        second = run_monte_carlo(make_spec(), scenario)
        assert first.returns == second.returns
        assert first.worst_drawdown == second.worst_drawdown

    def test_report_survives_extreme_overlay(self) -> None:
        scenario = scenario_with_overlays(
            [
                {
                    "type": "gap_injection",
                    "params": {
                        "kind": "flash_crash",
                        "magnitude_pct": -25.0,
                        "recovery_bars": 2,
                        "start": 60,
                    },
                }
            ]
        )
        report = run_monte_carlo(make_spec(), scenario)
        # all returns finite even when the crash dominates
        assert all(math.isfinite(r) for r in report.returns)

    def test_repetitions_param_bounded(self) -> None:
        scenario = Scenario.parse(
            {
                "scenario_id": "x",
                "window": {"symbol": "B", "timeframe": "5m", "frame": make_frame(60)},
                "overlays": [],
                "seed": 1,
                "repetitions": 4,
            }
        )
        with pytest.raises(ValueError):
            run_monte_carlo(make_spec(), scenario, max_repetitions=3)


class TestAttribution:
    CRASH = {
        "type": "gap_injection",
        "params": {
            "kind": "flash_crash",
            "magnitude_pct": -15.0,
            "recovery_bars": 10,
            "start": 100,
        },
    }
    FREEZE = {"type": "stale_feed", "params": {"duration_bars": 20, "start": 40}}

    def test_single_overlay_effect_is_marginal(self) -> None:
        breakdown = attribute_overlays(make_spec(), [self.CRASH])
        assert isinstance(breakdown, AttributionBreakdown)
        assert list(breakdown.effects) == ["gap_injection"]
        assert math.isfinite(breakdown.effects["gap_injection"])
        assert breakdown.base_final_equity > 0

    def test_two_overlays_additive_with_interaction(self) -> None:
        breakdown = attribute_overlays(make_spec(), [self.CRASH, self.FREEZE])
        assert set(breakdown.effects) == {"gap_injection", "stale_feed"}
        assert math.isfinite(breakdown.interaction)
        # additive check: base + Σ effects + interaction == full result
        total = (
            breakdown.base_final_equity
            + sum(breakdown.effects.values())
            + breakdown.interaction
        )
        assert total == pytest.approx(breakdown.combined_final_equity, rel=1e-9)

    def test_no_overlays_gives_flat_breakdown(self) -> None:
        breakdown = attribute_overlays(make_spec(), [])
        assert breakdown.effects == {}
        assert breakdown.interaction == 0.0
        assert breakdown.combined_final_equity == pytest.approx(
            breakdown.base_final_equity
        )

    def test_breakdown_records_overlay_types_in_order(self) -> None:
        # duplicate types aggregate by type name
        breakdown = attribute_overlays(
            make_spec(), [self.CRASH, self.FREEZE, self.CRASH]
        )
        assert list(breakdown.effects) == ["gap_injection", "stale_feed"]

    def test_effect_sign_matches_intuition(self) -> None:
        # a flash crash during a long-biased window should reduce final equity
        breakdown = attribute_overlays(make_spec(), [self.CRASH])
        if breakdown.base_final_equity != breakdown.combined_final_equity:
            # crash-only window equals base + crash effect (no interaction)
            assert breakdown.effects["gap_injection"] < 0.0
