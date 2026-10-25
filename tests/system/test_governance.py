from pathlib import Path

import pytest

from trading_gym.execution.governance.ledger import AuditLedger
from trading_gym.execution.governance.models import (
    ConsumeRequest,
    DecisionRequest,
    ProposalCreate,
    ProposalState,
    RiskTier,
)
from trading_gym.execution.governance.service import GovernanceConflict, GovernanceService
from trading_gym.execution.governance.triggers import (
    TriggerOperator,
    TriggerRule,
    evaluate_trigger,
)


def service(tmp_path: Path) -> GovernanceService:
    return GovernanceService(
        tmp_path / "proposals.json", AuditLedger(tmp_path / "audit.jsonl")
    )


def test_proposal_requires_closed_transitions_and_is_one_time(tmp_path: Path) -> None:
    governance = service(tmp_path)
    proposal = governance.create(
        ProposalCreate(
            operation="trade.paper.start",
            summary="Start bounded paper session",
            payload={"api_key": "must-redact"},
            risk_tier=RiskTier.MEDIUM,
            idempotency_key="paper-start-001",
        ),
        actor="agent",
    )
    duplicate = governance.create(
        ProposalCreate(
            operation="trade.paper.start",
            summary="Start bounded paper session",
            payload={"api_key": "must-redact"},
            risk_tier=RiskTier.MEDIUM,
            idempotency_key="paper-start-001",
        ),
        actor="agent",
    )
    assert duplicate.proposal_id == proposal.proposal_id
    with pytest.raises(GovernanceConflict):
        governance.create(
            ProposalCreate(
                operation="trade.paper.stop",
                summary="Different request",
                payload={},
                risk_tier=RiskTier.MEDIUM,
                idempotency_key="paper-start-001",
            ),
            actor="agent",
        )
    approved = governance.decide(
        proposal.proposal_id,
        DecisionRequest(expected_revision=1, actor="human", decision="approve"),
    )
    consumed = governance.consume(
        proposal.proposal_id,
        ConsumeRequest(expected_revision=approved.revision, actor="execution-gateway"),
    )
    assert consumed.state is ProposalState.CONSUMED
    with pytest.raises(GovernanceConflict):
        governance.consume(
            proposal.proposal_id,
            ConsumeRequest(expected_revision=consumed.revision, actor="replay"),
        )
    assert governance.ledger.verify()["valid"] is True
    assert "must-redact" not in (tmp_path / "audit.jsonl").read_text()


def test_audit_tampering_is_detected(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    ledger.append(event="test", actor="operator", subject="x", details={"value": 1})
    content = (tmp_path / "audit.jsonl").read_text()
    (tmp_path / "audit.jsonl").write_text(content.replace('"value":1', '"value":2'))
    assert ledger.verify()["valid"] is False


def test_trigger_only_yields_a_proposal() -> None:
    rule = TriggerRule(
        trigger_id="drawdown_guard",
        metric="drawdown",
        operator=TriggerOperator.GTE,
        threshold=0.1,
        operation="risk.pause",
        summary="Pause new risk after drawdown threshold",
        risk_tier=RiskTier.HIGH,
    )
    proposal = evaluate_trigger(
        rule, {"drawdown": 0.15}, evaluation_bucket="2026-07-26T23:00"
    )
    assert proposal is not None
    assert proposal.operation == "risk.pause"
    assert not hasattr(proposal, "execute")
