import hashlib
import json
from datetime import datetime, timedelta, timezone

from trading_gym.governance import (
    ControlDefinition,
    ControlEffectivenessRequest,
    ControlTestEvidence,
    evaluate_control_effectiveness,
)
from trading_gym.operations import (
    DependencyImpactRequest,
    ForensicEvidence,
    ForensicReadinessRequest,
    ForensicTimelineEvent,
    RunbookReadinessRequest,
    RunbookStep,
    ServiceDependencyNode,
    evaluate_dependency_impact,
    evaluate_forensic_readiness,
    evaluate_runbook_readiness,
)

NOW = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


def _receipt(**kwargs) -> str:
    return hashlib.sha256(
        json.dumps(kwargs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _evidence(
    evidence_id: str,
    source: str,
    previous: str | None,
    classification: str = "internal",
    redacted: bool = True,
):
    payload = {
        "evidence_id": evidence_id,
        "source_id": source,
        "collected_at": NOW.isoformat(),
        "content_sha256": ("a" if evidence_id == "e1" else "b") * 64,
        "size_bytes": 100,
        "classification": classification,
        "immutable": True,
        "redacted": redacted,
        "previous_receipt_sha256": previous,
    }
    return ForensicEvidence(**payload, receipt_sha256=_receipt(**payload))


def test_forensic_readiness_validates_chain_timeline_and_redaction_without_export():
    e1 = _evidence("e1", "audit", None)
    e2 = _evidence(
        "e2", "broker", e1.receipt_sha256, classification="restricted", redacted=True
    )
    report = evaluate_forensic_readiness(
        ForensicReadinessRequest(
            incident_id="inc-1",
            evaluated_at=NOW + timedelta(seconds=1),
            evidence=[e1, e2],
            required_source_ids=("audit", "broker"),
            timeline=[
                ForensicTimelineEvent(
                    event_id="t1",
                    occurred_at=NOW,
                    source_id="audit",
                    summary="order rejected",
                    evidence_ids=("e1", "e2"),
                )
            ],
        )
    )
    assert report.ready_for_forensic_review
    assert report.covered_sources == ["audit", "broker"]
    assert not report.export_authority_granted


def test_forensic_readiness_rejects_broken_chain_unredacted_and_missing_evidence():
    e1 = _evidence("e1", "audit", None)
    e2 = _evidence(
        "e2", "broker", "f" * 64, classification="restricted", redacted=False
    )
    report = evaluate_forensic_readiness(
        ForensicReadinessRequest(
            incident_id="inc-1",
            evaluated_at=NOW,
            evidence=[e1, e2],
            required_source_ids=("audit", "broker", "metrics"),
            timeline=[
                ForensicTimelineEvent(
                    event_id="t1",
                    occurred_at=NOW,
                    source_id="audit",
                    summary="gap",
                    evidence_ids=("missing",),
                )
            ],
        )
    )
    assert not report.ready_for_forensic_review
    joined = "\n".join(report.blockers)
    assert "custody_chain_discontinuity:e2" in joined
    assert "sensitive_evidence_unredacted:e2" in joined
    assert "required_source_missing:metrics" in joined
    assert "timeline_evidence_missing:t1:missing" in joined


def test_control_effectiveness_requires_independent_current_evidence_and_risk_coverage():
    report = evaluate_control_effectiveness(
        ControlEffectivenessRequest(
            evaluated_at=NOW,
            controls=[
                ControlDefinition(
                    control_id="c1",
                    objective="Prevent self approval",
                    risk_id="r1",
                    owner_role="governance",
                    frequency="event",
                    automated=True,
                    preventive=True,
                )
            ],
            tests=[
                ControlTestEvidence(
                    test_id="t1",
                    control_id="c1",
                    tested_at=NOW,
                    tester_role="audit",
                    sample_size=10,
                    exceptions=0,
                    result="pass",
                    evidence_sha256="a" * 64,
                )
            ],
            required_risk_ids=("r1",),
        )
    )
    assert report.ready_for_governance_review
    assert report.effective_controls == ["c1"]
    assert not report.remediation_authority_granted


def test_control_effectiveness_blocks_self_test_failure_and_uncovered_risk():
    report = evaluate_control_effectiveness(
        ControlEffectivenessRequest(
            evaluated_at=NOW,
            controls=[
                ControlDefinition(
                    control_id="c1",
                    objective="Prevent self approval",
                    risk_id="r1",
                    owner_role="governance",
                    frequency="event",
                    automated=True,
                    preventive=True,
                )
            ],
            tests=[
                ControlTestEvidence(
                    test_id="t1",
                    control_id="c1",
                    tested_at=NOW,
                    tester_role="governance",
                    sample_size=10,
                    exceptions=1,
                    result="fail",
                    evidence_sha256="a" * 64,
                )
            ],
            required_risk_ids=("r1", "r2"),
        )
    )
    assert not report.ready_for_governance_review
    joined = "\n".join(report.blockers)
    assert "control_tester_not_independent:c1" in joined
    assert "control_failed:c1" in joined
    assert "risk_uncovered:r2" in joined


def test_dependency_impact_finds_transitive_blast_radius_and_headroom():
    report = evaluate_dependency_impact(
        DependencyImpactRequest(
            change_id="change-1",
            services=[
                ServiceDependencyNode(
                    service_id="db",
                    criticality="critical",
                    dependencies=(),
                    capacity_units=100,
                    observed_load_units=50,
                    rollback_available=True,
                    owner_role="data",
                ),
                ServiceDependencyNode(
                    service_id="api",
                    criticality="critical",
                    dependencies=("db",),
                    capacity_units=100,
                    observed_load_units=60,
                    rollback_available=True,
                    owner_role="platform",
                ),
                ServiceDependencyNode(
                    service_id="ui",
                    criticality="medium",
                    dependencies=("api",),
                    capacity_units=100,
                    observed_load_units=30,
                    rollback_available=True,
                    owner_role="product",
                ),
            ],
            directly_changed_services=("db",),
            maximum_critical_blast_radius=2,
        )
    )
    assert report.ready_for_change_review
    assert report.impacted_services == ["api", "db", "ui"]
    assert report.impact_paths["ui"] == ["db", "api", "ui"]
    assert not report.deployment_authority_granted


def test_dependency_impact_blocks_cycle_capacity_and_missing_rollback():
    report = evaluate_dependency_impact(
        DependencyImpactRequest(
            change_id="change-1",
            services=[
                ServiceDependencyNode(
                    service_id="a",
                    criticality="critical",
                    dependencies=("b",),
                    capacity_units=100,
                    observed_load_units=95,
                    rollback_available=False,
                    owner_role="x",
                ),
                ServiceDependencyNode(
                    service_id="b",
                    criticality="critical",
                    dependencies=("a",),
                    capacity_units=100,
                    observed_load_units=50,
                    rollback_available=True,
                    owner_role="y",
                ),
            ],
            directly_changed_services=("a",),
            maximum_critical_blast_radius=0,
        )
    )
    assert not report.ready_for_change_review
    joined = "\n".join(report.blockers)
    assert "dependency_cycle:" in joined
    assert "critical_blast_radius_exceeded:2" in joined
    assert "capacity_headroom_insufficient:a" in joined
    assert "rollback_unavailable:a" in joined


def _healthy_runbook():
    return RunbookReadinessRequest(
        runbook_id="live-stop",
        version=1,
        risk_level="critical",
        evaluated_at=NOW,
        last_rehearsed_at=NOW - timedelta(days=1),
        required_roles=("operator", "reviewer"),
        steps=[
            RunbookStep(
                step_id="diagnose",
                order=1,
                kind="diagnostic",
                owner_role="operator",
                instruction="Inspect sealed audit status",
                precondition="Incident declared",
                success_evidence="Status captured",
                timeout_seconds=60,
            ),
            RunbookStep(
                step_id="change",
                order=2,
                kind="manual_change",
                owner_role="operator",
                instruction="Apply approved stop",
                precondition="Approval receipt exists",
                success_evidence="Worker stopped",
                timeout_seconds=60,
                destructive=True,
                rollback_step_id="rollback",
            ),
            RunbookStep(
                step_id="verify",
                order=3,
                kind="verification",
                owner_role="reviewer",
                instruction="Verify no open worker",
                precondition="Stop attempted",
                success_evidence="Process inventory clear",
                timeout_seconds=60,
            ),
            RunbookStep(
                step_id="rollback",
                order=4,
                kind="rollback",
                owner_role="operator",
                instruction="Use governed restart workflow",
                precondition="Rollback approved",
                success_evidence="Worker healthy",
                timeout_seconds=120,
            ),
            RunbookStep(
                step_id="notify",
                order=5,
                kind="communication",
                owner_role="reviewer",
                instruction="Publish incident update",
                precondition="State verified",
                success_evidence="Update receipt",
                timeout_seconds=60,
            ),
        ],
    )


def test_runbook_requires_rehearsal_rollback_and_role_separation_without_execution():
    report = evaluate_runbook_readiness(_healthy_runbook())
    assert report.ready_for_operational_review
    assert report.roles == ["operator", "reviewer"]
    assert not report.execution_authority_granted


def test_runbook_rejects_literal_secret_and_stale_rehearsal():
    request = _healthy_runbook()
    steps = list(request.steps)
    steps[1] = steps[1].model_copy(
        update={"instruction": "Set api_key=supersecretvalue", "rollback_step_id": None}
    )
    broken = request.model_copy(
        update={"steps": steps, "last_rehearsed_at": NOW - timedelta(days=1000)}
    )
    report = evaluate_runbook_readiness(broken)
    assert not report.ready_for_operational_review
    joined = "\n".join(report.blockers)
    assert "literal_secret_detected:change" in joined
    assert "destructive_step_without_rollback:change" in joined
    assert "rehearsal_stale" in joined


def test_runbook_detects_literal_secret_in_success_evidence():
    request = _healthy_runbook()
    steps = list(request.steps)
    steps[0] = steps[0].model_copy(
        update={"success_evidence": "token=leakedcredentialvalue"}
    )
    report = evaluate_runbook_readiness(request.model_copy(update={"steps": steps}))
    assert not report.ready_for_operational_review
    assert "literal_secret_detected:diagnose" in report.blockers
