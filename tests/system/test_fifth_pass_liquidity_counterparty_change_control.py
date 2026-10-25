from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_gym.core.operations.change_control import (
    ChangeControlRequest,
    ChangeObservation,
    evaluate_change_control,
)
from trading_gym.execution.risk.liquidity import (
    LiquidityPosition,
    LiquidityRiskPolicy,
    LiquidityRiskRequest,
    evaluate_liquidity_risk,
)
from trading_gym.execution.treasury.counterparty import (
    CounterpartyExposure,
    CounterpartyRiskRequest,
    evaluate_counterparty_risk,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def test_liquidity_risk_blocks_unexitable_concentrated_position():
    report = evaluate_liquidity_risk(
        LiquidityRiskRequest(
            portfolio_equity=Decimal("100000"),
            positions=[
                LiquidityPosition(
                    instrument_id="MICROCAP-GBP",
                    quantity=Decimal("100000"),
                    mark_price=Decimal("1"),
                    average_daily_volume=Decimal("10000"),
                    maximum_participation_fraction=Decimal("0.10"),
                    spread_bps=Decimal("100"),
                    impact_bps_at_max_participation=Decimal("250"),
                    daily_volatility_fraction=Decimal("0.05"),
                )
            ],
            policy=LiquidityRiskPolicy(
                maximum_exit_days=Decimal("5"),
                maximum_cost_bps=Decimal("300"),
                maximum_single_position_illiquid_fraction=Decimal("0.25"),
            ),
        )
    )
    assert not report.allowed
    assert "MICROCAP-GBP:exit_horizon_exceeded" in report.violations
    assert "MICROCAP-GBP:illiquid_concentration_exceeded" in report.violations
    assert not report.execution_authority_granted


def test_counterparty_risk_applies_netting_collateral_haircut_and_wrong_way_risk():
    report = evaluate_counterparty_risk(
        CounterpartyRiskRequest(
            exposures=[
                CounterpartyExposure(
                    counterparty_id="broker-a",
                    receivable=Decimal("1000"),
                    payable=Decimal("200"),
                    posted_collateral=Decimal("300"),
                    collateral_haircut_fraction=Decimal("0.20"),
                    wrong_way_risk_multiplier=Decimal("1.5"),
                    credit_limit=Decimal("1000"),
                    overdue_receivable=Decimal("50"),
                    netting_set_id="netting-a",
                )
            ],
            maximum_total_exposure=Decimal("1000"),
        )
    )
    assert report.allowed
    assert report.lines[0].net_unsecured_exposure == Decimal("560.00")
    assert report.lines[0].adjusted_exposure == Decimal("840.000")
    assert not report.settlement_authority_granted


def test_counterparty_risk_blocks_limit_and_overdue_breach():
    report = evaluate_counterparty_risk(
        CounterpartyRiskRequest(
            exposures=[
                CounterpartyExposure(
                    counterparty_id="broker-b",
                    receivable=Decimal("1000"),
                    payable=Decimal("0"),
                    posted_collateral=Decimal("0"),
                    wrong_way_risk_multiplier=Decimal("2"),
                    credit_limit=Decimal("1000"),
                    overdue_receivable=Decimal("500"),
                )
            ],
            maximum_total_exposure=Decimal("1500"),
            maximum_overdue_fraction=Decimal("0.10"),
        )
    )
    assert not report.allowed
    assert "broker-b:credit_limit_exceeded" in report.violations
    assert "broker-b:overdue_fraction_exceeded" in report.violations
    assert "aggregate_counterparty_limit_exceeded" in report.violations


def test_change_control_requires_governance_rollback_and_healthy_canary():
    report = evaluate_change_control(
        ChangeControlRequest(
            change_id="change-platform-0001",
            evaluated_at=NOW,
            starts_at=NOW + timedelta(hours=1),
            ends_at=NOW + timedelta(hours=2),
            risk="critical",
            affected_components=["api", "execution"],
            rollback_steps=["restore previous image"],
            canary_fraction=0.20,
            approved_proposal_id=None,
            tested_rollback=False,
            observations=[
                ChangeObservation(
                    metric="error_rate",
                    baseline=0.01,
                    canary=0.05,
                    maximum_relative_degradation=0.5,
                    higher_is_better=False,
                )
            ],
        )
    )
    assert not report.ready_for_governance_review
    assert "governance_approval_required" in report.blockers
    assert "tested_rollback_required" in report.blockers
    assert "critical_canary_fraction_too_large" in report.blockers
    assert "canary_degradation:error_rate" in report.blockers
    assert not report.deployment_authority_granted
