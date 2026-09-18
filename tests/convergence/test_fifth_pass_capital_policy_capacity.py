from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading_gym.governance.policy_change import (
    PolicyDecisionSample,
    PolicyImpactRequest,
    PolicyVersion,
    evaluate_policy_change,
)
from trading_gym.operations.capacity import (
    CapacityClass,
    CapacityPlanRequest,
    evaluate_capacity,
)
from trading_gym.treasury import (
    CapitalRequirement,
    CollateralAllocationRequest,
    CollateralAsset,
    allocate_collateral,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def test_collateral_allocation_applies_haircuts_liquidity_and_concentration():
    request = CollateralAllocationRequest(
        assets=[
            CollateralAsset(
                asset_id="cash-gbp-01",
                account_id="acct-primary",
                market_value=Decimal("1000"),
                haircut_fraction=Decimal("0"),
                liquidity_score=Decimal("1"),
            ),
            CollateralAsset(
                asset_id="bond-gbp-01",
                account_id="acct-primary",
                market_value=Decimal("1000"),
                haircut_fraction=Decimal("0.20"),
                encumbered_value=Decimal("100"),
                pending_settlement_value=Decimal("100"),
                liquidity_score=Decimal("0.8"),
            ),
        ],
        requirement=CapitalRequirement(
            requirement_id="margin-requirement-01",
            required_value=Decimal("1200"),
            minimum_liquidity_score=Decimal("0.5"),
            maximum_single_asset_fraction=Decimal("0.6"),
        ),
    )
    report = allocate_collateral(request)
    assert report.allocated_value == Decimal("1200")
    assert report.shortfall == 0
    assert [line.allocated_value for line in report.lines] == [
        Decimal("720.0"),
        Decimal("480.0"),
    ]
    assert not report.blockers
    assert not report.transfer_authority_granted


def test_collateral_shortfall_is_explicit():
    report = allocate_collateral(
        CollateralAllocationRequest(
            assets=[
                CollateralAsset(
                    asset_id="illiquid-asset-01",
                    account_id="acct-primary",
                    market_value=Decimal("100"),
                    haircut_fraction=Decimal("0.5"),
                    liquidity_score=Decimal("0.1"),
                )
            ],
            requirement=CapitalRequirement(
                requirement_id="margin-requirement-02",
                required_value=Decimal("100"),
                minimum_liquidity_score=Decimal("0.5"),
            ),
        )
    )
    assert report.shortfall == Decimal("100")
    assert report.blockers == ["collateral_shortfall:100"]


def _policies():
    current = PolicyVersion(
        policy_id="risk-policy",
        version=1,
        rules={"max_leverage": 2},
        created_by="risk-owner",
    )
    candidate = PolicyVersion(
        policy_id="risk-policy",
        version=2,
        rules={"max_leverage": 3},
        created_by="risk-owner",
        parent_sha256=current.sha256,
    )
    return current, candidate


def test_policy_change_blocks_unapproved_relaxation_and_high_risk_mismatch():
    current, candidate = _policies()
    report = evaluate_policy_change(
        PolicyImpactRequest(
            current=current,
            candidate=candidate,
            samples=[
                PolicyDecisionSample(
                    sample_id="critical-leverage-case",
                    current_decision="deny",
                    candidate_decision="allow",
                    expected_decision="deny",
                    risk_tier="critical",
                ),
                PolicyDecisionSample(
                    sample_id="normal-case",
                    current_decision="allow",
                    candidate_decision="allow",
                ),
            ],
            maximum_change_fraction=0.75,
        )
    )
    assert not report.ready_for_governance_review
    assert "policy_relaxation_not_allowed:1" in report.blockers
    assert "high_risk_expectation_mismatch:critical-leverage-case" in report.blockers
    assert not report.activation_authority_granted


def test_policy_lineage_is_hash_bound():
    current, candidate = _policies()
    with pytest.raises(ValueError, match="parent hash"):
        PolicyImpactRequest(
            current=current,
            candidate=candidate.model_copy(update={"parent_sha256": "0" * 64}),
            samples=[
                PolicyDecisionSample(
                    sample_id="case-1",
                    current_decision="deny",
                    candidate_decision="deny",
                )
            ],
        )


def test_capacity_requires_backpressure_before_overload():
    report = evaluate_capacity(
        CapacityPlanRequest(
            workers=4,
            target_utilisation=0.70,
            maximum_total_queue=100,
            classes=[
                CapacityClass(
                    class_id="market-data",
                    arrival_rate_per_second=10,
                    average_service_seconds=0.5,
                    burst_size=200,
                    queue_limit=50,
                    priority=10,
                ),
                CapacityClass(
                    class_id="research-jobs",
                    arrival_rate_per_second=0.1,
                    average_service_seconds=5,
                    burst_size=2,
                    queue_limit=10,
                ),
            ],
        )
    )
    assert report.required_workers == 8
    assert any(
        item.startswith("capacity_utilisation_exceeded") for item in report.blockers
    )
    assert "backpressure_required:market-data" in report.warnings
    assert not report.scaling_authority_granted


def test_policy_change_detects_version_only_noop():
    current = PolicyVersion(
        policy_id="risk-policy",
        version=1,
        rules={"max_leverage": 2},
        created_by="owner",
    )
    candidate = PolicyVersion(
        policy_id="risk-policy",
        version=2,
        rules={"max_leverage": 2},
        created_by="owner",
        parent_sha256=current.sha256,
    )
    report = evaluate_policy_change(
        PolicyImpactRequest(
            current=current,
            candidate=candidate,
            samples=[
                PolicyDecisionSample(
                    sample_id="unchanged-case",
                    current_decision="deny",
                    candidate_decision="deny",
                )
            ],
        )
    )
    assert "policy_candidate_has_no_effective_change" in report.blockers
