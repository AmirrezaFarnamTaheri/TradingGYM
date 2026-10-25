from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_gym.core.institutional import (
    ChaosExperiment,
    ChaosFault,
    CollaborationReview,
    ComplianceControlMapping,
    DistributedChaosRequest,
    EnterpriseRiskPolicy,
    EnterpriseRiskRequest,
    EventRecord,
    FederatedWorkUnit,
    MarketplacePackage,
    PolicyComplianceRequest,
    PolicyOperator,
    PolicyRule,
    ProductCollaborationRequest,
    RFCArtifact,
    ReviewState,
    RiskDomain,
    RiskExposure,
    TerminalWidget,
    WidgetKind,
    WorkerHealth,
    WorkerNode,
    evaluate_distributed_chaos,
    evaluate_enterprise_risk,
    evaluate_policy_compliance,
    evaluate_product_collaboration,
)
from trading_gym.core.institutional.base import sha256_payload

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
H = "a" * 64


def test_enterprise_risk_aggregates_domains_groups_and_explanations() -> None:
    report = evaluate_enterprise_risk(
        EnterpriseRiskRequest(
            exposures=[
                RiskExposure(
                    exposure_id="m1",
                    domain=RiskDomain.MARKET,
                    gross_loss_base=Decimal("100"),
                    probability=0.2,
                    correlation_group="macro",
                    owner="risk",
                ),
                RiskExposure(
                    exposure_id="c1",
                    domain=RiskDomain.CREDIT,
                    gross_loss_base=Decimal("50"),
                    probability=0.1,
                    correlation_group="venue",
                    owner="treasury",
                    mitigated_fraction=Decimal("0.2"),
                ),
            ],
            policy=EnterpriseRiskPolicy(
                maximum_expected_loss_base=Decimal("30"),
                maximum_single_exposure_base=Decimal("200"),
                maximum_domain_fraction=Decimal("0.9"),
            ),
        )
    )
    assert report.expected_loss_base == Decimal("24.00")
    assert report.top_contributors[0] == "m1"
    assert report.explanations
    assert not report.execution_authority_granted


def test_enterprise_risk_blocks_limits_and_concentration() -> None:
    report = evaluate_enterprise_risk(
        EnterpriseRiskRequest(
            exposures=[
                RiskExposure(
                    exposure_id="m1",
                    domain=RiskDomain.MARKET,
                    gross_loss_base=Decimal("100"),
                    probability=1.0,
                    correlation_group="g",
                    owner="risk",
                )
            ],
            policy=EnterpriseRiskPolicy(
                maximum_expected_loss_base=Decimal("50"),
                maximum_single_exposure_base=Decimal("90"),
                maximum_domain_fraction=Decimal("0.5"),
            ),
        )
    )
    assert "expected_loss_limit" in report.blockers
    assert "single_exposure_limit:m1" in report.blockers
    assert "domain_concentration:market" in report.blockers


def test_policy_compliance_requires_rules_rfc_and_control_evidence() -> None:
    report = evaluate_policy_compliance(
        PolicyComplianceRequest(
            policy_id="p1",
            rfc_id="rfc1",
            evaluated_at=NOW,
            context={"risk": {"leverage": 2.0}, "change": {"rollback": True}},
            rules=[
                PolicyRule(
                    rule_id="r1",
                    field="risk.leverage",
                    operator=PolicyOperator.LE,
                    expected=3.0,
                    message="Leverage is within the approved cap.",
                ),
                PolicyRule(
                    rule_id="r2",
                    field="change.rollback",
                    operator=PolicyOperator.EQ,
                    expected=True,
                    message="Rollback evidence exists.",
                ),
            ],
            rfc_artifacts=[
                RFCArtifact(
                    artifact_type="security-review", sha256=H, approved_by=["security"]
                )
            ],
            required_rfc_artifacts=["security-review"],
            control_mappings=[
                ComplianceControlMapping(
                    internal_control_id="AC-1",
                    framework="SOC2",
                    framework_control="CC6.1",
                    evidence_sha256="b" * 64,
                    tested=True,
                    passed=True,
                )
            ],
            required_frameworks=["SOC2"],
        )
    )
    assert report.ready_for_governance_review
    assert not report.policy_activation_authority_granted
    assert not report.compliance_conclusion_reached


def test_policy_compliance_hard_veto_and_missing_framework() -> None:
    report = evaluate_policy_compliance(
        PolicyComplianceRequest(
            policy_id="p1",
            rfc_id="rfc1",
            evaluated_at=NOW,
            context={"risk": {"leverage": 5}},
            rules=[
                PolicyRule(
                    rule_id="r1",
                    field="risk.leverage",
                    operator=PolicyOperator.LE,
                    expected=3,
                    message="Leverage cap",
                )
            ],
            rfc_artifacts=[],
            required_rfc_artifacts=["rollback"],
            control_mappings=[],
            required_frameworks=["ISO27001"],
        )
    )
    assert "policy_veto:r1" in report.blockers
    assert "rfc_artifacts_missing" in report.blockers
    assert "framework_unmapped:ISO27001" in report.blockers


def _event(
    stream: str, sequence: int, event_type: str, previous: str, payload: dict
) -> EventRecord:
    data = {
        "stream_id": stream,
        "sequence": sequence,
        "event_type": event_type,
        "payload": payload,
        "previous_sha256": previous,
        "occurred_at": NOW,
    }
    digest = sha256_payload(data)
    return EventRecord(
        stream_id=stream,
        sequence=sequence,
        event_type=event_type,
        payload=payload,
        previous_sha256=previous,
        event_sha256=digest,
        occurred_at=NOW,
    )


def test_distributed_runtime_assigns_with_fencing_and_replays_events() -> None:
    first = _event("s1", 1, "created", "0" * 64, {"x": 1})
    second = _event("s1", 2, "updated", first.event_sha256, {"x": 2})
    report = evaluate_distributed_chaos(
        DistributedChaosRequest(
            workers=[
                WorkerNode(
                    worker_id="w1",
                    zone="a",
                    capabilities=["research"],
                    capacity_units=10,
                    fencing_token=5,
                    health=WorkerHealth.HEALTHY,
                ),
                WorkerNode(
                    worker_id="w2",
                    zone="b",
                    capabilities=["research"],
                    capacity_units=10,
                    fencing_token=6,
                    health=WorkerHealth.DEGRADED,
                ),
            ],
            work_units=[
                FederatedWorkUnit(
                    work_id="job1",
                    required_capabilities=["research"],
                    required_units=3,
                    preferred_zone="a",
                )
            ],
            events=[first, second],
            chaos_experiments=[
                ChaosExperiment(
                    experiment_id="c1",
                    fault=ChaosFault.LATENCY,
                    target="provider",
                    blast_radius=0.05,
                    duration_seconds=30,
                    abort_conditions=["error budget exhausted"],
                    rollback_sha256=H,
                )
            ],
        )
    )
    assert report.assignments[0].worker_id == "w1"
    assert report.assignments[0].fencing_token == 5
    assert report.event_stream_heads["s1"] == second.event_sha256
    assert report.chaos_ready_ids == ["c1"]
    assert not report.chaos_injection_performed


def test_distributed_runtime_blocks_bad_chain_cycle_and_live_chaos() -> None:
    bad = EventRecord(
        stream_id="s1",
        sequence=2,
        event_type="bad",
        payload={},
        previous_sha256="0" * 64,
        event_sha256=H,
        occurred_at=NOW,
    )
    report = evaluate_distributed_chaos(
        DistributedChaosRequest(
            workers=[],
            work_units=[
                FederatedWorkUnit(
                    work_id="aa",
                    required_capabilities=[],
                    required_units=1,
                    dependency_work_ids=["bb"],
                ),
                FederatedWorkUnit(
                    work_id="bb",
                    required_capabilities=[],
                    required_units=1,
                    dependency_work_ids=["aa"],
                ),
            ],
            events=[bad],
            chaos_experiments=[
                ChaosExperiment(
                    experiment_id="c1",
                    fault=ChaosFault.STORAGE_FAILURE,
                    target="db",
                    blast_radius=0.01,
                    duration_seconds=1,
                    abort_conditions=["any loss"],
                    rollback_sha256=H,
                    dry_run=False,
                )
            ],
        )
    )
    assert "work_dependency_cycle" in report.blockers
    assert any(item.startswith("event_sequence_gap") for item in report.blockers)
    assert "chaos_must_be_dry_run:c1" in report.blockers


def test_product_collaboration_admits_safe_packages_and_requires_reviews() -> None:
    report = evaluate_product_collaboration(
        ProductCollaborationRequest(
            evaluated_at=NOW,
            widgets=[
                TerminalWidget(
                    widget_id="risk",
                    kind=WidgetKind.RISK,
                    title="Risk",
                    evidence_sha256=H,
                )
            ],
            packages=[
                MarketplacePackage(
                    package_id="template",
                    version="1",
                    manifest_sha256=H,
                    archive_sha256="b" * 64,
                    signature_verified=True,
                    license_id="MIT",
                    requested_permissions=["read_market"],
                    executable_code=False,
                ),
                MarketplacePackage(
                    package_id="plugin",
                    version="1",
                    manifest_sha256="c" * 64,
                    archive_sha256="d" * 64,
                    signature_verified=False,
                    license_id="MIT",
                    requested_permissions=["submit_order"],
                    executable_code=True,
                ),
            ],
            reviews=[
                CollaborationReview(
                    review_id="r1",
                    subject_sha256=H,
                    author="alice",
                    reviewers=["bob", "carol"],
                    approvals=["bob", "carol"],
                    state=ReviewState.APPROVED,
                    updated_at=NOW,
                )
            ],
            allowed_marketplace_permissions=["read_market"],
            required_review_approvals=2,
        )
    )
    assert report.marketplace_admissible_packages == ["template:1"]
    assert "plugin:1" in report.marketplace_rejected_packages
    assert report.review_ready_ids == ["r1"]
    assert not report.package_installation_authority_granted


def test_product_collaboration_rejects_authoritative_ui_and_self_approval() -> None:
    report = evaluate_product_collaboration(
        ProductCollaborationRequest(
            evaluated_at=NOW,
            widgets=[
                TerminalWidget(
                    widget_id="bad",
                    kind=WidgetKind.GOVERNANCE,
                    title="Bad",
                    evidence_sha256=H,
                    authoritative=True,
                )
            ],
            packages=[],
            reviews=[],
            allowed_marketplace_permissions=[],
        )
    )
    assert "authoritative_ui_widget:bad" in report.blockers
    try:
        CollaborationReview(
            review_id="r",
            subject_sha256=H,
            author="alice",
            reviewers=["alice"],
            approvals=["alice"],
            state=ReviewState.APPROVED,
            updated_at=NOW,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("self approval must fail")
