"""M15: observation identity and source dependence are executable contracts."""

import numpy as np
import pytest
from trading_gym.agent.arms.base import (
    ArmProposal,
    ArmRegistry,
    ArmTier,
    BackendArm,
    CostModel,
)
from trading_gym.agent.arms.factory import default_registry
from trading_gym.agent.fusion import FusionEngine, FusionConfig, FusionWeights
from trading_gym.agent.planner import TierCascadePlanner
from trading_gym.authz.grants import GrantLedger
from trading_gym.intelligence.rl_backend import RLPolicyBackend
from trading_gym.intelligence.types import (
    ActionKind,
    DecisionSignal,
    MarketContext,
    InvalidDecisionSignal,
    BackendHealth,
)
from trading_gym.intelligence.base import IntelligenceBackend

CTX = MarketContext("BTC", 0, 1000, None, 100, 99)
COST = CostModel(1, 2)


def proposal(name, source, confidence=0.25, leverage=1):
    signal = DecisionSignal(
        name,
        ActionKind.LONG if leverage > 0 else ActionKind.SHORT,
        leverage,
        confidence,
        "test",
        source_id=source,
    )
    return ArmProposal(name, ArmTier.T1_RULES, signal, COST, source_id=source)


def test_copied_source_cannot_cross_quorum_or_raise_weight():
    engine = FusionEngine(FusionConfig(weights=FusionWeights({("alias", "x"): 100})))
    one = proposal("original", "same")
    two = proposal("alias", "same")
    assert engine.fuse([one], CTX, regime="x").escalated
    assert engine.fuse([one, two], CTX, regime="x").escalated
    assert engine.fuse([two, one], CTX, regime="x").escalated


def test_conflicting_source_aliases_are_rejected():
    out = FusionEngine().fuse(
        [proposal("a", "same", 0.8, 1), proposal("b", "same", 0.8, -1)], CTX, regime="x"
    )
    assert out.escalated and "source_conflict" in out.rationale


def test_distinct_sources_are_votes_not_probabilities():
    out = FusionEngine().fuse([proposal("a", "a"), proposal("b", "b")], CTX, regime="x")
    assert not out.escalated and out.confidence == 1
    assert out.confidence_semantics == "vote_agreement"


def test_default_registry_has_one_guarded_rules_source():
    registry = default_registry()
    assert (
        len(
            [
                a
                for a in registry.arms()
                if getattr(a, "source_id", None) == "risk-guard:rules"
            ]
        )
        == 1
    )


class Model:
    def __init__(self):
        self.observations = []

    def predict(self, obs, deterministic=True):
        self.observations.append(obs)
        return np.array([1.25]), None


def test_planner_rl_receives_exact_observation_not_synthetic_context(tmp_path):
    model = Model()
    registry = ArmRegistry()
    registry.register(
        BackendArm(RLPolicyBackend(model), tier=ArmTier.T3_MODEL, cost=COST)
    )
    planner = TierCascadePlanner(registry)
    ledger = GrantLedger(tmp_path / "grants.jsonl")
    no_obs = planner.run(CTX, regime="x", principal="test", ledger=ledger)
    assert no_obs.fused.escalated and model.observations == []
    obs = np.array([10.0, 20.0, 0.0])
    result = planner.run(
        CTX, regime="x", principal="test", ledger=ledger, observation=obs
    )
    assert result.fused.target_leverage == 1.25
    assert model.observations[0] is obs


class Broken(IntelligenceBackend):
    name = "broken"

    def __init__(self, error):
        self.error = error

    def is_available(self):
        return True

    def health(self):
        return BackendHealth(self.name, True)

    def decide(self, context):
        raise self.error


@pytest.mark.parametrize("error", [InvalidDecisionSignal("bad"), ValueError("bad")])
def test_planner_does_not_bench_invalid_evidence_and_trade_elsewhere(tmp_path, error):
    registry = ArmRegistry()
    registry.register(BackendArm(Broken(error), tier=ArmTier.T1_RULES, cost=COST))
    with pytest.raises(PermissionError):
        TierCascadePlanner(registry).run(
            CTX,
            regime="x",
            principal="test",
            ledger=GrantLedger(tmp_path / "grants.jsonl"),
        )
