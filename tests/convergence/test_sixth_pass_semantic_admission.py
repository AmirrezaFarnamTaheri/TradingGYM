from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import math
import pytest

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)
H = "a" * 64


def test_alpha_factory_rejects_duplicate_evidence_markets_and_infeasible_policy() -> (
    None
):
    from trading_gym.institutional_os.alpha_factory import (
        AlphaEvidence,
        AlphaFactoryPolicy,
        AlphaHypothesis,
    )

    evidence = AlphaEvidence(
        evidence_id="e1",
        kind="backtest",
        sha256=H,
        observed_at=NOW,
        supports_hypothesis=True,
    )
    common = dict(
        hypothesis_id="hypothesis",
        statement="A measurable market effect should persist after costs.",
        mechanism="The mechanism is constrained supply interacting with bounded demand.",
        falsification_criteria=["Fails after costs"],
        target_markets=["BTC-USD"],
        expected_horizon="one-day",
    )
    with pytest.raises(ValueError, match="evidence IDs must be unique"):
        AlphaHypothesis(**common, evidence=[evidence, evidence])
    with pytest.raises(ValueError, match="target markets must be unique"):
        AlphaHypothesis(
            **{**common, "target_markets": ["BTC-USD", "BTC-USD"]}, evidence=[evidence]
        )
    with pytest.raises(ValueError, match="independent evidence requirement"):
        AlphaFactoryPolicy(
            minimum_supporting_evidence=1, minimum_independent_evidence=2
        )


def test_model_lifecycle_rejects_duplicate_requirements_and_evidence() -> None:
    from trading_gym.institutional_os.model_lifecycle import (
        LifecycleEvidence,
        LifecycleState,
        LifecycleTransitionRequest,
    )

    evidence = LifecycleEvidence(
        evidence_type="validation", sha256=H, observed_at=NOW, passed=True
    )
    with pytest.raises(
        ValueError, match="required lifecycle evidence types must be unique"
    ):
        LifecycleTransitionRequest(
            model_id="model",
            current_state=LifecycleState.TRAINED,
            requested_state=LifecycleState.VALIDATED,
            evidence=[evidence],
            evaluated_at=NOW,
            required_evidence_types=["validation", "validation"],
        )
    with pytest.raises(ValueError, match="evidence records must be unique"):
        LifecycleTransitionRequest(
            model_id="model",
            current_state=LifecycleState.TRAINED,
            requested_state=LifecycleState.VALIDATED,
            evidence=[evidence, evidence],
            evaluated_at=NOW,
        )


def test_microstructure_rejects_duplicate_price_levels() -> None:
    from trading_gym.institutional_os.microstructure import (
        BookLevel,
        BookSide,
        MicrostructureRequest,
    )

    level = BookLevel(price=Decimal("100"), visible_quantity=Decimal("1"))
    with pytest.raises(ValueError, match="prices must be unique"):
        MicrostructureRequest(
            side=BookSide.BUY,
            quantity=Decimal("1"),
            reference_price=Decimal("100"),
            levels=[level, level],
        )


def test_explainability_rejects_numeric_amplification_and_uses_stable_uncertainty() -> (
    None
):
    from trading_gym.institutional_os.explainability import (
        ExplainabilityRequest,
        FeatureContribution,
        explain_prediction,
    )

    with pytest.raises(ValueError, match="numeric magnitude"):
        ExplainabilityRequest(
            model_id="model",
            features=[
                FeatureContribution(feature="feature", value=1e101, coefficient=1.0)
            ],
        )
    report = explain_prediction(
        ExplainabilityRequest(
            model_id="model",
            features=[
                FeatureContribution(feature="feature", value=1e100, coefficient=1.0)
            ],
            predictive_samples=[1e100, -1e100],
        )
    )
    assert math.isfinite(report.raw_score)
    assert report.predictive_mean == 0.0
    assert report.predictive_stddev == pytest.approx(1e100)
    assert not report.decision_authority_granted


def test_policy_compliance_rejects_secret_values_and_duplicate_governance_evidence() -> (
    None
):
    from trading_gym.institutional_os.policy_compliance import (
        ComplianceControlMapping,
        PolicyComplianceRequest,
        PolicyOperator,
        PolicyRule,
        RFCArtifact,
    )

    with pytest.raises(ValueError, match="credential-like"):
        PolicyRule(
            rule_id="rule",
            field="risk.limit",
            operator=PolicyOperator.EQ,
            expected={"api_token": "secret"},
            message="Risk limit must match policy.",
        )
    with pytest.raises(ValueError, match="RFC approvers must be unique"):
        RFCArtifact(artifact_type="design", sha256=H, approved_by=["alice", "alice"])

    rule = PolicyRule(
        rule_id="rule",
        field="risk.limit",
        operator=PolicyOperator.EQ,
        expected=1,
        message="Risk limit must match policy.",
    )
    artifact = RFCArtifact(artifact_type="design", sha256=H, approved_by=["alice"])
    control = ComplianceControlMapping(
        internal_control_id="control",
        framework="SOC2",
        framework_control="CC1",
        evidence_sha256=H,
        tested=True,
        passed=True,
    )
    base = dict(
        policy_id="policy",
        context={"risk": {"limit": 1}},
        rules=[rule],
        rfc_id="rfc",
        rfc_artifacts=[artifact],
        required_rfc_artifacts=["design"],
        control_mappings=[control],
        required_frameworks=["SOC2"],
        evaluated_at=NOW,
    )
    with pytest.raises(ValueError, match="required RFC artifact types must be unique"):
        PolicyComplianceRequest(
            **{**base, "required_rfc_artifacts": ["design", "design"]}
        )
    with pytest.raises(ValueError, match="control mappings must be unique"):
        PolicyComplianceRequest(**{**base, "control_mappings": [control, control]})
