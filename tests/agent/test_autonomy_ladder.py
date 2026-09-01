"""AXIOM-1 autonomy ladder (ADR-032): governed agent authority levels.

Contract:
- Levels L0_SHADOW → L1_PAPER → L2_TINY_LIVE → L3_SCALED; skip-level
  promotion is structurally impossible.
- Promotion requires level-specific evidence: L0→L1 needs a full shadow
  window (>= min_shadow_days) with zero breaches and a minimum decision
  count; L1→L2 needs positive fusion lift (DSR) plus an approved
  critical proposal; L2→L3 needs sustained SLO evidence plus red-team
  closure. Missing/failed gates deny with a reason.
- Demotion is immediate and journaled on any breach; promotion attempts
  and level changes are appended to a hash-chained journal which fails
  its own open on tampering (same integrity pattern as the grant
  ledger).
- The governor never grants trading authority itself — it answers
  "what is this agent allowed to do right now"; execution authority
  still flows through the coordinator and grants.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_gym.agent.autonomy_ladder import (
    AutonomyLevel,
    AutonomyLedgerIntegrityError,
    PromotionEvidence,
    PromotionDenied,
    AutonomyGovernor,
)


SHADOW_START = "2026-06-01T00:00:00Z"


def strong_shadow_evidence(**overrides) -> PromotionEvidence:  # type: ignore[no-untyped-def]
    base: dict[str, object] = {
        "days_in_current_level": 30,
        "decisions_at_level": 500,
        "constraint_breaches": 0,
        "journal_valid": True,
        "slippage_within_tolerance": True,
    }
    base.update(overrides)
    return PromotionEvidence(**base)  # type: ignore[arg-type]


class TestPromotionGates:
    def test_starts_at_shadow(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        assert governor.level is AutonomyLevel.L0_SHADOW

    def test_shadow_to_paper_requires_full_window(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        with pytest.raises(PromotionDenied) as excinfo:
            governor.promote(strong_shadow_evidence(days_in_current_level=10))
        assert "shadow window" in str(excinfo.value)

    def test_shadow_to_paper_denied_on_breaches(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        with pytest.raises(PromotionDenied):
            governor.promote(strong_shadow_evidence(constraint_breaches=1))

    def test_shadow_to_paper_happy_path(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        governor.promote(strong_shadow_evidence())
        assert governor.level is AutonomyLevel.L1_PAPER

    def test_paper_to_live_requires_positive_lift_and_proposal(
        self, tmp_path: Path
    ) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        governor.promote(strong_shadow_evidence())
        with pytest.raises(PromotionDenied):
            governor.promote(
                strong_shadow_evidence(fusion_lift_dsr=0.0, critical_proposal_id="prop-1")
            )
        with pytest.raises(PromotionDenied):
            governor.promote(
                strong_shadow_evidence(fusion_lift_dsr=0.15, critical_proposal_id="")
            )
        governor.promote(
            strong_shadow_evidence(fusion_lift_dsr=0.15, critical_proposal_id="prop-1")
        )
        assert governor.level is AutonomyLevel.L2_TINY_LIVE

    def test_live_to_scaled_requires_red_team_closure(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        governor.promote(strong_shadow_evidence())
        governor.promote(
            strong_shadow_evidence(fusion_lift_dsr=0.15, critical_proposal_id="prop-1")
        )
        with pytest.raises(PromotionDenied):
            governor.promote(
                strong_shadow_evidence(
                    fusion_lift_dsr=0.15,
                    critical_proposal_id="prop-1",
                    red_team_open_findings=2,
                )
            )
        governor.promote(
            strong_shadow_evidence(
                fusion_lift_dsr=0.15,
                critical_proposal_id="prop-1",
                red_team_open_findings=0,
            )
        )
        assert governor.level is AutonomyLevel.L3_SCALED

    def test_skip_level_promotion_rejected(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        with pytest.raises(PromotionDenied) as excinfo:
            governor.promote(
                strong_shadow_evidence(
                    fusion_lift_dsr=0.15,
                    critical_proposal_id="prop-1",
                    red_team_open_findings=0,
                )
            )
        assert "skip" in str(excinfo.value).lower() or "L2" in str(excinfo.value)

    def test_top_level_is_terminal(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        governor.promote(strong_shadow_evidence())
        governor.promote(
            strong_shadow_evidence(fusion_lift_dsr=0.15, critical_proposal_id="prop-1")
        )
        governor.promote(
            strong_shadow_evidence(
                fusion_lift_dsr=0.15,
                critical_proposal_id="prop-1",
                red_team_open_findings=0,
            )
        )
        with pytest.raises(PromotionDenied):
            governor.promote(
                strong_shadow_evidence(
                    fusion_lift_dsr=0.15,
                    critical_proposal_id="prop-1",
                    red_team_open_findings=0,
                )
            )


class TestDemotion:
    def test_breach_demotes_immediately(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        governor.promote(strong_shadow_evidence())
        assert governor.level is AutonomyLevel.L1_PAPER
        demoted = governor.demote(
            reason="constraint breach: max_orders_per_hour exceeded",
            actor="risk-gate",
        )
        assert demoted.level is AutonomyLevel.L0_SHADOW
        assert governor.level is AutonomyLevel.L0_SHADOW

    def test_demotion_at_shadow_is_noop_latch(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        demoted = governor.demote(reason="panic pressed", actor="operator-1")
        assert demoted.level is AutonomyLevel.L0_SHADOW

    def test_demotion_requires_repromotion_window(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        governor.promote(strong_shadow_evidence())
        governor.demote(reason="constraint breach", actor="risk-gate")
        # after a demotion, the shadow window restarts: fresh evidence at 30
        # days from a demotion timestamp is not yet available
        with pytest.raises(PromotionDenied):
            governor.promote(strong_shadow_evidence(days_in_current_level=5))


class TestAuthorityMapping:
    def test_level_authority_contract(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        assert governor.authority() == {
            "live_orders": False,
            "paper_orders": False,
            "journaled_decisions": True,
            "self_revision": "none",
        }
        governor.promote(strong_shadow_evidence())
        assert governor.authority()["paper_orders"] is True
        assert governor.authority()["live_orders"] is False
        governor.promote(
            strong_shadow_evidence(fusion_lift_dsr=0.15, critical_proposal_id="prop-1")
        )
        assert governor.authority()["live_orders"] is True
        assert governor.authority()["self_revision"] == "parametric"


class TestAutonomyJournal:
    def test_state_survives_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "autonomy.jsonl"
        governor = AutonomyGovernor.open(path)
        governor.promote(strong_shadow_evidence())
        reopened = AutonomyGovernor.open(path)
        assert reopened.level is AutonomyLevel.L1_PAPER

    def test_demotion_survives_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "autonomy.jsonl"
        governor = AutonomyGovernor.open(path)
        governor.promote(strong_shadow_evidence())
        governor.demote(reason="breach", actor="risk-gate")
        reopened = AutonomyGovernor.open(path)
        assert reopened.level is AutonomyLevel.L0_SHADOW

    def test_tampered_journal_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "autonomy.jsonl"
        governor = AutonomyGovernor.open(path)
        governor.promote(strong_shadow_evidence())

        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["level"] = "L3_SCALED"
        lines[0] = json.dumps(record, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with pytest.raises(AutonomyLedgerIntegrityError):
            AutonomyGovernor.open(path)

    def test_events_chain(self, tmp_path: Path) -> None:
        governor = AutonomyGovernor.open(tmp_path / "autonomy.jsonl")
        governor.promote(strong_shadow_evidence())
        governor.demote(reason="breach", actor="risk-gate")
        events = governor.events()
        assert [event["type"] for event in events] == [
            "promoted",
            "demoted",
        ]
        assert governor.verify()["valid"] is True
