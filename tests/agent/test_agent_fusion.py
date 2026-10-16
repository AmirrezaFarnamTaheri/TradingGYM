"""AXIOM-1 fusion engine v1 (ADR-030): regime-conditional weighted vote.

Contract:
- Input is one blackboard's proposals (arm name, signal, tier). Output is
  one fused ``DecisionSignal`` from a designated fusion identity.
- Weights are per (arm, regime-tag) and caller-supplied; unknown arms get
  zero weight; stale/absent arms contribute nothing.
- Direction vote is confidence-weighted by regime weight; magnitude is the
  weighted mean of agreeing arms' leverage, never extrapolated beyond the
  roster's max magnitude.
- Disagreement (direction split beyond a margin, or no arm above the
  quorum threshold) degrades to HOLD with an escalation flag — fusion
  never launders a split vote into a position.
- Calibration: a stale-freshness stamp can zero an arm's contribution.
"""

from __future__ import annotations

import math

import pytest

from trading_gym.agent.arms import ArmTier, CostModel
from trading_gym.agent.fusion import (
    DisagreementEscalated,
    FusionConfig,
    FusionEngine,
    FusionWeights,
)
from trading_gym.intelligence.types import (
    ActionKind,
    DecisionSignal,
    MarketContext,
)


def make_context() -> MarketContext:
    return MarketContext(
        symbol="BTCUSDT",
        position_units=0.0,
        equity=10_000.0,
        margin_level=None,
        last_close=50_000.0,
        prev_close=49_000.0,
        recent_returns=(0.01, 0.005, -0.002),
        drawdown_from_peak=0.05,
    )


def signal(
    backend: str,
    kind: ActionKind,
    leverage: float,
    confidence: float = 0.8,
) -> DecisionSignal:
    return DecisionSignal(
        backend=backend,
        action_kind=kind,
        target_leverage=leverage,
        confidence=confidence,
        rationale=f"{backend} says {kind.value}",
    )


def arm_proposal(name, signal_obj, tier=ArmTier.T1_RULES):  # type: ignore[no-untyped-def]
    from trading_gym.agent.arms import ArmProposal

    return ArmProposal(
        arm_name=name,
        tier=tier,
        signal=signal_obj,
        cost_paid=CostModel(latency_p50_ms=1, latency_p99_ms=2, credits_per_call=0),
    )


class TestFusionWeights:
    def test_default_weights_are_uniform(self) -> None:
        weights = FusionWeights.from_pairs((("rules", "trend"), 1.0))
        assert weights.for_arm("rules", "trend") == 1.0
        assert weights.for_arm("unknown-arm", "trend") == 0.0

    def test_negative_weights_rejected(self) -> None:
        with pytest.raises(ValueError):
            FusionWeights.from_pairs((("rules", "trend"), -0.5))

    def test_non_finite_rejected(self) -> None:
        for bad in (float("nan"), float("inf")):
            with pytest.raises(ValueError):
                FusionWeights.from_pairs((("rules", "trend"), bad))


class TestDirectionVote:
    def test_unanimous_long_fuses_long(self) -> None:
        engine = FusionEngine(FusionConfig())
        board = [
            arm_proposal("rules", signal("rules", ActionKind.LONG, 1.0)),
            arm_proposal("alpha", signal("alpha", ActionKind.LONG, 2.0)),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        assert fused.action_kind is ActionKind.LONG
        assert not fused.escalated

    def test_weighted_vote_respects_confidence(self) -> None:
        engine = FusionEngine(FusionConfig())
        board = [
            arm_proposal(
                "rules", signal("rules", ActionKind.LONG, 1.0, confidence=0.9)
            ),
            arm_proposal(
                "alpha", signal("alpha", ActionKind.SHORT, -1.0, confidence=0.1)
            ),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        assert fused.action_kind is ActionKind.LONG

    def test_uniform_weights_with_split_confidences_long_wins(self) -> None:
        engine = FusionEngine(FusionConfig())
        board = [
            arm_proposal(
                "rules", signal("rules", ActionKind.LONG, 1.0, confidence=0.9)
            ),
            arm_proposal(
                "alpha", signal("alpha", ActionKind.SHORT, -1.0, confidence=0.2)
            ),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        assert fused.action_kind is ActionKind.LONG

    def test_hold_proposals_neutral_in_vote(self) -> None:
        engine = FusionEngine(FusionConfig())
        board = [
            arm_proposal("rules", signal("rules", ActionKind.LONG, 1.0)),
            arm_proposal("defensive", signal("defensive", ActionKind.HOLD, 0.0)),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        assert fused.action_kind is ActionKind.LONG

    def test_all_hold_fuses_hold_without_escalation(self) -> None:
        engine = FusionEngine(FusionConfig())
        board = [
            arm_proposal("rules", signal("rules", ActionKind.HOLD, 0.0)),
            arm_proposal("defensive", signal("defensive", ActionKind.HOLD, 0.0)),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        assert fused.action_kind is ActionKind.HOLD
        assert not fused.escalated


class TestDisagreement:
    def test_split_vote_escalates_to_flat(self) -> None:
        engine = FusionEngine(FusionConfig(disagreement_margin=0.15))
        board = [
            arm_proposal(
                "rules", signal("rules", ActionKind.LONG, 1.0, confidence=0.7)
            ),
            arm_proposal(
                "bear", signal("bear", ActionKind.SHORT, -1.0, confidence=0.7)
            ),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        assert fused.action_kind is ActionKind.HOLD
        assert fused.escalated
        assert fused.target_leverage == 0.0
        assert (
            isinstance(fused.rationale, str) and "disagree" in fused.rationale.lower()
        )

    def test_quorum_failure_escalates(self) -> None:
        engine = FusionEngine(FusionConfig(quorum_confidence=0.9))
        board = [
            arm_proposal(
                "rules", signal("rules", ActionKind.LONG, 1.0, confidence=0.7)
            ),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        assert fused.action_kind is ActionKind.HOLD
        assert fused.escalated

    def test_strong_majority_does_not_escalate(self) -> None:
        engine = FusionEngine(FusionConfig(disagreement_margin=0.2))
        board = [
            arm_proposal(
                "rules", signal("rules", ActionKind.LONG, 1.0, confidence=0.9)
            ),
            arm_proposal(
                "alpha", signal("alpha", ActionKind.LONG, 2.0, confidence=0.8)
            ),
            arm_proposal(
                "bear", signal("bear", ActionKind.SHORT, -1.0, confidence=0.3)
            ),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        assert fused.action_kind is ActionKind.LONG
        assert not fused.escalated


class TestMagnitude:
    def test_magnitude_is_weighted_mean_of_agreeing_arms(self) -> None:
        engine = FusionEngine(FusionConfig())
        board = [
            arm_proposal(
                "rules", signal("rules", ActionKind.LONG, 1.0, confidence=0.9)
            ),
            arm_proposal(
                "alpha", signal("alpha", ActionKind.LONG, 3.0, confidence=0.9)
            ),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        assert fused.target_leverage == pytest.approx(2.0)

    def test_magnitude_never_exceeds_roster_ceiling(self) -> None:
        engine = FusionEngine(FusionConfig(max_leverage=5.0))
        board = [
            arm_proposal("rules", signal("rules", ActionKind.LONG, 4.0)),
            arm_proposal("alpha", signal("alpha", ActionKind.LONG, 20.0)),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        assert abs(fused.target_leverage) <= 5.0 + 1e-9

    def test_disagreeing_arm_excluded_from_magnitude(self) -> None:
        engine = FusionEngine(FusionConfig())
        board = [
            arm_proposal(
                "rules", signal("rules", ActionKind.LONG, 1.0, confidence=0.9)
            ),
            arm_proposal(
                "alpha", signal("alpha", ActionKind.LONG, 3.0, confidence=0.9)
            ),
            arm_proposal(
                "bear", signal("bear", ActionKind.SHORT, -10.0, confidence=0.05)
            ),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        assert fused.target_leverage == pytest.approx(2.0)


class TestRegimeWeights:
    def test_regime_selects_weight_column(self) -> None:
        weights = FusionWeights.from_pairs(
            (("rules", "trend"), 1.0), (("rules", "chop"), 0.2)
        )
        engine = FusionEngine(FusionConfig(weights=weights))

        long_board = [
            arm_proposal(
                "rules", signal("rules", ActionKind.LONG, 1.0, confidence=0.8)
            ),
            arm_proposal(
                "alpha", signal("alpha", ActionKind.LONG, 1.0, confidence=0.8)
            ),
        ]
        # chop: rules downweighted but still directional majority
        fused = engine.fuse(long_board, make_context(), regime="chop")
        assert fused.action_kind is ActionKind.LONG

    def test_zero_weight_arm_contributes_nothing(self) -> None:
        weights = FusionWeights.from_pairs((("bear", "trend"), 0.0))
        engine = FusionEngine(FusionConfig(weights=weights))
        board = [
            arm_proposal(
                "rules", signal("rules", ActionKind.LONG, 1.0, confidence=0.5)
            ),
            arm_proposal(
                "bear", signal("bear", ActionKind.SHORT, -2.0, confidence=0.95)
            ),
        ]
        fused = engine.fuse(board, make_context(), regime="trend")
        # zero-weighted bear cannot outvote the rules arm
        assert fused.action_kind is ActionKind.LONG


class TestEmptyRoster:
    def test_no_proposals_escalates_to_flat(self) -> None:
        engine = FusionEngine(FusionConfig())
        fused = engine.fuse([], make_context(), regime="trend")
        assert fused.action_kind is ActionKind.HOLD
        assert fused.escalated
        assert fused.target_leverage == 0.0

    def test_backend_label_is_fusion(self) -> None:
        engine = FusionEngine(FusionConfig())
        fused = engine.fuse(
            [arm_proposal("rules", signal("rules", ActionKind.LONG, 1.0))],
            make_context(),
            regime="trend",
        )
        assert fused.backend == "fusion"


class TestEscalationType:
    def test_escalated_result_is_subclass(self) -> None:
        engine = FusionEngine(FusionConfig())
        fused = engine.fuse([], make_context(), regime="trend")
        assert isinstance(fused, DisagreementEscalated)

def test_fusion_weight_snapshots_support_dataclass_projection_and_deepcopy() -> None:
    """Read-only snapshots must remain compatible with audit/copy tooling."""
    import copy
    import pickle
    from dataclasses import asdict

    raw = {("rules", "trend"): 1.0}
    weights = FusionWeights(entries=raw)
    raw[("rules", "trend")] = 0.0
    config = FusionConfig(weights=weights)

    projected = asdict(config)
    assert projected["weights"]["entries"][("rules", "trend")] == 1.0
    projected["weights"]["entries"][("rules", "trend")] = 0.1
    assert config.weights.effective("rules", "trend") == 1.0

    cloned = copy.deepcopy(config)
    restored = pickle.loads(pickle.dumps(config))
    assert cloned.weights == restored.weights == config.weights
    with pytest.raises(TypeError):
        cloned.weights.entries[("rules", "trend")] = 0.0  # type: ignore[index]

def test_contradictory_signed_direction_never_produces_an_order() -> None:
    engine = FusionEngine(FusionConfig())
    fused = engine.fuse(
        [arm_proposal("contradictory", signal("contradictory", ActionKind.LONG, -1.0))],
        make_context(),
        regime="trend",
    )
    assert fused.escalated and fused.action_kind is ActionKind.HOLD
    assert fused.target_leverage == 0.0
    assert "direction_leverage_mismatch" in fused.rationale


def test_zero_weight_disabled_arm_cannot_veto_a_valid_vote() -> None:
    weights = FusionWeights.from_pairs((("disabled", "trend"), 0.0))
    fused = FusionEngine(FusionConfig(weights=weights)).fuse(
        [
            arm_proposal("rules", signal("rules", ActionKind.LONG, 1.0)),
            arm_proposal("disabled", signal("disabled", ActionKind.LONG, -1.0)),
        ],
        make_context(),
        regime="trend",
    )
    assert not fused.escalated
    assert fused.action_kind is ActionKind.LONG
    assert fused.target_leverage == 1.0


def test_vote_mass_overflow_escalates_without_nonfinite_decision() -> None:
    weights = FusionWeights.from_pairs(
        (("rules", "trend"), 1e308),
        (("alpha", "trend"), 1e308),
    )
    fused = FusionEngine(FusionConfig(weights=weights)).fuse(
        [
            arm_proposal("rules", signal("rules", ActionKind.LONG, 1.0, 1.0)),
            arm_proposal("alpha", signal("alpha", ActionKind.LONG, 1.0, 1.0)),
        ],
        make_context(),
        regime="trend",
    )
    assert fused.escalated and fused.action_kind is ActionKind.HOLD
    assert math.isfinite(fused.confidence)
    assert math.isfinite(fused.target_leverage)


def test_weighted_exposure_overflow_escalates_instead_of_clamping() -> None:
    fused = FusionEngine(FusionConfig()).fuse(
        [
            arm_proposal("rules", signal("rules", ActionKind.LONG, 1e308, 1.0)),
            arm_proposal("alpha", signal("alpha", ActionKind.LONG, 1e308, 1.0)),
        ],
        make_context(),
        regime="trend",
    )
    assert fused.escalated and fused.action_kind is ActionKind.HOLD
    assert math.isfinite(fused.target_leverage)
