"""AXIOM-1 self-improvement revision surface (ADR-033).

Contract:
- Exactly five revision tiers (P parametric, S structural, M meta,
  B behavioral, X code/artifacts); the surface is the ONLY place that
  maps a revision kind to its authority requirement. X is never
  self-revisable — the agent can only nominate, never apply.
- Every revision passes the gate chain in order: sealed holdout (the
  holdout id must be pinned and unrevealed at hypothesis time),
  regression floor (candidate must not lose more than floor vs champion
  on the shared suite), diversity floor (pairwise arm-return correlation
  must stay above the monoculture threshold), and canary rejection
  (planted known-bad candidates must fail; a passing canary freezes the
  loop).
- Decisions (applied/rejected) append to a hash-chained revision ledger
  — the agent's auditable autobiography — which fails its open on
  tampering and records WHY for every rejection.
- Rate caps: no more than max_revisions_per_window applied per rolling
  window; a cooldown applies after each deploy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_gym.agent.revision import (
    RevisionKind,
    TierConfig,
    RevisionLedgerIntegrityError,
    RevisionNotAllowed,
    RevisionSurface,
    RevisionCandidate,
    GateChain,
    GateResult,
)


SEEDS = {"holdout-a": "sha-a", "holdout-b": "sha-b"}


def candidate(
    kind: RevisionKind = RevisionKind.PARAMETRIC,
    *,
    revision_id: str = "rev-001",
    champion: float = 1.0,
    suite: float = 1.02,
    holdout: float = 1.03,
    correlation: float = 0.2,
    is_canary: bool = False,
    canary_should_fail: bool = False,
) -> RevisionCandidate:
    return RevisionCandidate(
        revision_id=revision_id,
        kind=kind,
        target="rules-arm.thresholds",
        summary="raise defence threshold",
        suite_score=suite,
        champion_score=champion,
        sealed_holdouts={"holdout-a": holdout, "holdout-b": holdout},
        seeded_holdout_seals=SEEDS,
        pairwise_correlation=correlation,
        is_canary=is_canary,
        canary_expected_to_fail=canary_should_fail,
    )


class TestRevisionSurface:
    def test_parametric_auto_applies(self, tmp_path: Path) -> None:
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        outcome = surface.submit(candidate())
        assert outcome.applied is True
        assert outcome.gates.passed

    def test_structural_requires_holdout_replication(self, tmp_path: Path) -> None:
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        outcome = surface.submit(
            candidate(RevisionKind.STRUCTURAL, holdout=0.98)
        )
        assert outcome.applied is False
        assert outcome.gates.failed_gate == "sealed_holdout"
        assert outcome.reason == "sealed_holdout"

    def test_meta_requires_stronger_lift(self, tmp_path: Path) -> None:
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        weak = candidate(RevisionKind.META, suite=1.01, holdout=1.01)
        outcome = surface.submit(weak)
        assert outcome.applied is False
        assert outcome.reason == "lift_below_tier_threshold"

    def test_code_tier_is_never_self_revisable(self, tmp_path: Path) -> None:
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        with pytest.raises(RevisionNotAllowed):
            surface.submit(candidate(RevisionKind.CODE))

    def test_unsealed_holdout_rejected(self, tmp_path: Path) -> None:
        surface = RevisionSurface.open(tmp_path / "revisions.jsonl")
        bad = candidate()
        sealed = dict(bad.seeded_holdout_seals)
        sealed["holdout-rogue"] = "sha-rogue"
        unsealed = RevisionCandidate(
            revision_id=bad.revision_id,
            kind=bad.kind,
            target=bad.target,
            summary=bad.summary,
            suite_score=bad.suite_score,
            champion_score=bad.champion_score,
            sealed_holdouts=bad.sealed_holdouts,
            seeded_holdout_seals=sealed,
            pairwise_correlation=bad.pairwise_correlation,
            is_canary=False,
            canary_expected_to_fail=False,
        )
        outcome = surface.submit(unsealed)
        assert outcome.applied is False
        assert outcome.gates.failed_gate == "sealed_holdout"
        assert "holdout-rogue" in outcome.gates.detail


class TestGateChain:
    def _tier(self) -> TierConfig:
        return RevisionSurface.default_tiers()[RevisionKind.PARAMETRIC]

    def test_regression_floor_rejects_loss(self, tmp_path: Path) -> None:
        chain = GateChain()
        result = chain.evaluate(
            candidate(suite=0.9),
            tier_config=self._tier(),
        )
        assert result.passed is False
        assert result.failed_gate == "regression_floor"

    def test_diversity_floor_rejects_monoculture(self, tmp_path: Path) -> None:
        chain = GateChain()
        result = chain.evaluate(
            candidate(correlation=0.95),
            tier_config=self._tier(),
        )
        assert result.passed is False
        assert result.failed_gate == "diversity_floor"

    def test_canary_passing_freezes(self, tmp_path: Path) -> None:
        chain = GateChain()
        result = chain.evaluate(
            candidate(is_canary=True, canary_should_fail=False),
            tier_config=self._tier(),
        )
        assert result.passed is False
        assert result.failed_gate == "canary"
        assert "integrity failure" in result.detail

    def test_canary_failing_as_expected_passes_gate(self, tmp_path: Path) -> None:
        chain = GateChain()
        result = chain.evaluate(
            candidate(is_canary=True, canary_should_fail=True),
            tier_config=self._tier(),
        )
        # a canary that correctly fails is only rejected as a normal candidate
        assert result.failed_gate in (None, "regression_floor") or result.passed

    def test_gate_order_is_documented(self) -> None:
        assert GateChain.order() == (
            "sealed_holdout",
            "regression_floor",
            "diversity_floor",
            "canary",
        )


class TestRevisionLedger:
    def test_decisions_are_journaled_and_replayed(self, tmp_path: Path) -> None:
        path = tmp_path / "revisions.jsonl"
        surface = RevisionSurface.open(path)
        surface.submit(candidate())
        surface.submit(candidate(revision_id="rev-002", suite=0.5))

        reopened = RevisionSurface.open(path)
        history = reopened.history()
        assert [entry["decision"] for entry in history] == ["applied", "rejected"]
        assert history[1]["reason"] == "regression_floor"

    def test_tampered_ledger_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "revisions.jsonl"
        surface = RevisionSurface.open(path)
        surface.submit(candidate())

        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["decision"] = "rejected"
        lines[0] = json.dumps(record, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(RevisionLedgerIntegrityError):
            RevisionSurface.open(path)

    def test_rate_cap_enforced(self, tmp_path: Path) -> None:
        surface = RevisionSurface.open(
            tmp_path / "revisions.jsonl", max_revisions_per_day=2
        )
        assert surface.submit(candidate(revision_id="r1")).applied
        assert surface.submit(candidate(revision_id="r2")).applied
        outcome = surface.submit(candidate(revision_id="r3"))
        assert outcome.applied is False
        assert outcome.reason == "rate_cap"
