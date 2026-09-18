"""AXIOM-1 ensemble correlation (round 6): the diversity gate's data path.

Contract:
- ``arm_exposure_series`` maps one arm over a context sequence to its
  signed per-bar exposure (target_leverage).
- ``ensemble_pairwise_correlation`` is the mean pairwise Pearson
  correlation across arms with non-constant series; constant arms
  (pure-HOLD) are excluded, and fewer than two varying arms returns
  None (no correlation dimension).
- The planner-level bridge ``RevisionEvidenceBuilder`` ties the
  playground runs, the arm registry, and the ADR-033 candidate
  together: suite/holdout scores from real runs, pairwise correlation
  measured over the candidate's decision sequence — removing the
  bridge's safe-default correlation.
"""

from __future__ import annotations


import numpy as np
import pandas as pd
import pytest

from trading_gym.agent.ensemble_correlation import (
    arm_exposure_series,
    ensemble_pairwise_correlation,
)
from trading_gym.agent.arms import ArmRegistry, ArmTier, BackendArm, CostModel
from trading_gym.intelligence.rule_backend import DeterministicRuleBackend
from trading_gym.intelligence.types import (
    ActionKind,
    DecisionSignal,
    MarketContext,
)


def make_context(momentum: float) -> MarketContext:
    return MarketContext(
        symbol="BTCUSDT",
        position_units=0.0,
        equity=10_000.0,
        margin_level=None,
        last_close=50_000.0,
        prev_close=49_500.0,
        recent_returns=(momentum, momentum / 2, momentum / 4),
        drawdown_from_peak=0.03,
    )


class _LongFollow(DeterministicRuleBackend):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    def decide(self, context: MarketContext) -> DecisionSignal:
        return DecisionSignal(
            backend=self.name,
            action_kind=(
                ActionKind.LONG if sum(context.recent_returns) > 0 else ActionKind.SHORT
            ),
            target_leverage=1.0 if sum(context.recent_returns) > 0 else -1.0,
            confidence=0.8,
            rationale="follow",
        )


class _Inverse(DeterministicRuleBackend):
    def __init__(self) -> None:
        super().__init__()
        self.name = "inverse"

    def decide(self, context: MarketContext) -> DecisionSignal:
        up = sum(context.recent_returns) > 0
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind.SHORT if up else ActionKind.LONG,
            target_leverage=-1.0 if up else 1.0,
            confidence=0.8,
            rationale="inverse",
        )


class _AlwaysHold(DeterministicRuleBackend):
    def __init__(self) -> None:
        super().__init__()
        self.name = "hold"

    def decide(self, context: MarketContext) -> DecisionSignal:
        return DecisionSignal(
            backend=self.name,
            action_kind=ActionKind.HOLD,
            target_leverage=0.0,
            confidence=0.5,
            rationale="hold",
        )


def _arm(backend, tier: ArmTier = ArmTier.T1_RULES) -> BackendArm:  # type: ignore[no-untyped-def]
    return BackendArm(
        backend=backend,
        tier=tier,
        cost=CostModel(latency_p50_ms=0.1, latency_p99_ms=0.5),
        required_scopes=frozenset(),
    )


class TestExposureSeries:
    def test_series_follows_signals(self) -> None:
        arm = _arm(_LongFollow("a"))
        contexts = [make_context(0.01), make_context(-0.01), make_context(0.01)]
        series = arm_exposure_series(arm, contexts)
        assert series == [1.0, -1.0, 1.0]


class TestEnsembleCorrelation:
    def test_perfectly_correlated_arms(self) -> None:
        registry = ArmRegistry()
        registry.register(_arm(_LongFollow("a")))
        registry.register(_arm(_LongFollow("b")))
        contexts = [make_context(0.01), make_context(-0.01), make_context(0.01)]
        corr = ensemble_pairwise_correlation(registry, contexts)
        assert corr is not None
        assert corr == pytest.approx(1.0)

    def test_inverse_arms_have_negative_correlation(self) -> None:
        registry = ArmRegistry()
        registry.register(_arm(_LongFollow("a")))
        registry.register(_arm(_Inverse()))
        contexts = [make_context(0.01), make_context(-0.01), make_context(0.01)]
        corr = ensemble_pairwise_correlation(registry, contexts)
        assert corr is not None
        assert corr == pytest.approx(-1.0)

    def test_constant_arms_excluded(self) -> None:
        registry = ArmRegistry()
        registry.register(_arm(_LongFollow("a")))
        registry.register(_arm(_LongFollow("b")))
        registry.register(_arm(_AlwaysHold()))
        contexts = [make_context(0.01), make_context(-0.01), make_context(0.01)]
        corr = ensemble_pairwise_correlation(registry, contexts)
        assert corr == pytest.approx(1.0)  # hold arm contributed nothing

    def test_no_varying_arms_returns_none(self) -> None:
        registry = ArmRegistry()
        registry.register(_arm(_AlwaysHold()))
        contexts = [make_context(0.01), make_context(-0.01)]
        assert ensemble_pairwise_correlation(registry, contexts) is None

    def test_short_sequence_returns_none(self) -> None:
        registry = ArmRegistry()
        registry.register(_arm(_LongFollow("a")))
        assert ensemble_pairwise_correlation(registry, [make_context(0.01)]) is None


class TestRevisionEvidenceBuilder:
    """The full ADR-031→033 path with measured (not default) correlation."""

    def test_builder_measures_real_correlation(self, tmp_path) -> None:  # type: ignore[no-untyped-def]

        from trading_gym.agent.revision import RevisionKind, RevisionSurface
        from trading_gym.playground.bridge import RevisionEvidenceBuilder
        from trading_gym.playground.runner import PlaygroundRunSpec
        from trading_gym.strategy_lab.models import (
            Condition,
            ConditionGroup,
            IndicatorSpec,
            Operand,
            Operator,
            StrategySpec,
        )

        def make_frame(seed: int) -> pd.DataFrame:
            rng = np.random.default_rng(seed)
            index = pd.date_range("2024-07-01", periods=240, freq="5min", tz="UTC")
            close = 50_000 * np.exp(np.cumsum(rng.normal(0.0002, 0.0015, 240)))
            open_ = np.roll(close, 1)
            open_[0] = 50_000.0
            high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0004, 240)))
            low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0004, 240)))
            return pd.DataFrame(
                {
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": rng.uniform(10, 1000, 240),
                    "spread_bps": np.full(240, 2.0),
                },
                index=index,
            )

        strategy = StrategySpec(
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

        def make_spec(seed: int, *, fee_bps: float = 10.0) -> PlaygroundRunSpec:
            return PlaygroundRunSpec(
                window=make_frame(seed),
                strategy=strategy,
                initial_capital=10_000.0,
                fee_bps=fee_bps,
            )

        windows = {"hold-a": make_frame(11)}
        builder = RevisionEvidenceBuilder(
            registry=_build_roster(inverse=True),
            contexts=[make_context(m) for m in (0.01, -0.008, 0.012, -0.006, 0.009)],
        )
        revision = builder.build(
            revision_id="rev-r6-1",
            kind=RevisionKind.PARAMETRIC,
            target="rules-arm.thresholds",
            summary="fee tweak",
            champion=make_spec(11),
            candidate=make_spec(11, fee_bps=9.0),
            windows=windows,
        )
        # measured inverse-roster correlation, not the 0.2 default
        assert revision.pairwise_correlation == pytest.approx(-1.0)

        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        outcome = surface.submit(revision)
        assert outcome.gates.passed is True


class TestMonocultureRejection:
    def test_correlated_roster_fails_diversity_gate(self, tmp_path) -> None:  # type: ignore[no-untyped-def]

        from trading_gym.agent.revision import RevisionKind, RevisionSurface
        from trading_gym.playground.bridge import RevisionEvidenceBuilder
        from trading_gym.playground.runner import PlaygroundRunSpec
        from trading_gym.strategy_lab.models import (
            Condition,
            ConditionGroup,
            IndicatorSpec,
            Operand,
            Operator,
            StrategySpec,
        )

        rng = np.random.default_rng(11)
        index = pd.date_range("2024-07-01", periods=240, freq="5min", tz="UTC")
        close = 50_000 * np.exp(np.cumsum(rng.normal(0.0002, 0.0015, 240)))
        open_ = np.roll(close, 1)
        open_[0] = 50_000.0
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0004, 240)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0004, 240)))
        frame = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": rng.uniform(10, 1000, 240),
                "spread_bps": np.full(240, 2.0),
            },
            index=index,
        )
        strategy = StrategySpec(
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

        def spec(fee_bps: float) -> PlaygroundRunSpec:
            return PlaygroundRunSpec(
                window=frame,
                strategy=strategy,
                initial_capital=10_000.0,
                fee_bps=fee_bps,
            )

        builder = RevisionEvidenceBuilder(
            registry=_build_roster(inverse=False),
            contexts=[make_context(m) for m in (0.01, -0.008, 0.012, -0.006, 0.009)],
        )
        revision = builder.build(
            revision_id="rev-r6-2",
            kind=RevisionKind.PARAMETRIC,
            target="t",
            summary="s",
            champion=spec(10.0),
            candidate=spec(9.0),
            windows={"hold-a": frame},
        )
        assert revision.pairwise_correlation == pytest.approx(1.0)
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        outcome = surface.submit(revision)
        assert outcome.gates.failed_gate == "diversity_floor"


def _build_roster(inverse: bool = False) -> ArmRegistry:
    registry = ArmRegistry()
    registry.register(_arm(_LongFollow("a")))
    registry.register(_arm(_Inverse() if inverse else _LongFollow("b")))
    return registry
