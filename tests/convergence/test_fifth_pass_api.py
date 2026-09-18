import hashlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.convergence import create_convergence_router

NOW = "2026-07-27T12:00:00Z"


def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("AUDIT_HMAC_KEY", "a" * 32)
    app = FastAPI()
    app.include_router(create_convergence_router(lambda: True), prefix="/api/v1")
    return TestClient(app)


def test_coordination_replay_policy_and_capacity_api(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    acquired = api.post(
        "/api/v1/convergence/coordination/leases",
        json={
            "lease_id": "lease-api-0001",
            "resource_id": "resource-api-market",
            "owner_id": "worker-api-primary",
            "idempotency_key": "idem-api-lease-0001",
            "ttl_seconds": 60,
            "acquired_at": NOW,
        },
    )
    assert acquired.status_code == 200, acquired.text
    assert acquired.json()["fencing_token"] == 1
    assert acquired.json()["execution_authority_granted"] is False

    event1 = {
        "stream_id": "stream-api-01",
        "sequence": 1,
        "event_id": "event-api-0001",
        "event_type": "cash.credited",
        "occurred_at": NOW,
        "previous_hash": "0" * 64,
        "event_hash": "0" * 64,
        "payload": {"amount": "10"},
    }
    replay = api.post("/api/v1/convergence/events/replay", json={"events": [event1]})
    assert replay.status_code == 200
    assert replay.json()["valid"] is False
    assert replay.json()["mutation_performed"] is False

    current = {
        "policy_id": "risk-api",
        "version": 1,
        "rules": {"max_leverage": 2},
        "created_by": "owner",
        "parent_sha256": None,
    }
    from trading_gym.governance.policy_change import PolicyVersion

    current_sha = PolicyVersion(**current).sha256
    impact = api.post(
        "/api/v1/convergence/governance/policies/impact",
        json={
            "current": current,
            "candidate": {
                "policy_id": "risk-api",
                "version": 2,
                "rules": {"max_leverage": 1.5},
                "created_by": "owner",
                "parent_sha256": current_sha,
            },
            "samples": [
                {
                    "sample_id": "sample-safe-01",
                    "current_decision": "review",
                    "candidate_decision": "deny",
                    "expected_decision": "deny",
                    "risk_tier": "high",
                }
            ],
            "maximum_change_fraction": 1.0,
        },
    )
    assert impact.status_code == 200, impact.text
    assert impact.json()["ready_for_governance_review"] is True
    assert impact.json()["activation_authority_granted"] is False

    capacity = api.post(
        "/api/v1/convergence/operations/capacity/evaluate",
        json={
            "workers": 10,
            "target_utilisation": 0.7,
            "maximum_total_queue": 100,
            "classes": [
                {
                    "class_id": "api-jobs",
                    "arrival_rate_per_second": 1,
                    "average_service_seconds": 1,
                    "burst_size": 5,
                    "queue_limit": 20,
                }
            ],
        },
    )
    assert capacity.status_code == 200
    assert capacity.json()["blockers"] == []
    assert capacity.json()["scaling_authority_granted"] is False


def test_capital_surveillance_privacy_and_failover_api(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    collateral = api.post(
        "/api/v1/convergence/treasury/collateral/allocate",
        json={
            "assets": [
                {
                    "asset_id": "cash-gbp-api",
                    "account_id": "acct-api",
                    "market_value": "1000",
                    "haircut_fraction": "0",
                    "encumbered_value": "0",
                    "pending_settlement_value": "0",
                    "liquidity_score": "1",
                    "eligible": True,
                }
            ],
            "requirement": {
                "requirement_id": "capital-api-01",
                "required_value": "500",
                "minimum_liquidity_score": "0.5",
                "maximum_single_asset_fraction": "1",
            },
        },
    )
    assert collateral.status_code == 200
    assert collateral.json()["shortfall"] == "0"
    assert collateral.json()["transfer_authority_granted"] is False

    surveillance = api.post(
        "/api/v1/convergence/compliance/surveillance/evaluate",
        json={
            "executions": [
                {
                    "execution_id": "exec-api-001",
                    "side": "buy",
                    "quantity": "1",
                    "execution_price": "100",
                    "benchmark_price": "100",
                    "fee_amount": "0",
                    "occurred_at": NOW,
                }
            ],
            "trades": [],
            "maximum_adverse_bps": 10,
        },
    )
    assert surveillance.status_code == 200
    assert surveillance.json()["blockers"] == []
    assert surveillance.json()["legal_conclusion_reached"] is False

    privacy = api.post(
        "/api/v1/convergence/privacy/minimisation/evaluate",
        json={
            "evaluated_at": NOW,
            "fields": [
                {
                    "field_id": "public.market_symbol",
                    "store_id": "catalogue",
                    "classification": "public",
                    "purpose": "instrument routing",
                    "retention_days": 3650,
                    "collected_at": "2026-01-01T00:00:00Z",
                    "encrypted_at_rest": False,
                    "access_roles": ["operator"],
                    "subject_linkable": False,
                    "legal_hold": False,
                    "required_for_operation": True,
                }
            ],
        },
    )
    assert privacy.status_code == 200
    assert privacy.json()["blockers"] == []
    assert privacy.json()["mutation_performed"] is False

    failover = api.post(
        "/api/v1/convergence/operations/failover/evaluate",
        json={
            "evaluated_at": NOW,
            "replicas": [
                {
                    "replica_id": "primary-api-01",
                    "region": "eu-west",
                    "role": "primary",
                    "healthy": False,
                    "writable": False,
                    "observed_at": NOW,
                    "replication_lag_seconds": 0,
                    "last_sequence": 10,
                    "state_sha256": "a" * 64,
                },
                {
                    "replica_id": "replica-api-01",
                    "region": "uk-south",
                    "role": "replica",
                    "healthy": True,
                    "writable": True,
                    "observed_at": NOW,
                    "replication_lag_seconds": 1,
                    "last_sequence": 10,
                    "state_sha256": "a" * 64,
                },
            ],
            "required_healthy_regions": 1,
        },
    )
    assert failover.status_code == 200
    assert failover.json()["ready_for_failover_review"] is True
    assert failover.json()["failover_authority_granted"] is False


def test_liquidity_counterparty_change_and_sequential_api(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    liquidity = api.post(
        "/api/v1/convergence/risk/liquidity/evaluate",
        json={
            "portfolio_equity": "100000",
            "positions": [
                {
                    "instrument_id": "BTCGBP",
                    "quantity": "1",
                    "mark_price": "50000",
                    "average_daily_volume": "1000",
                    "maximum_participation_fraction": "0.1",
                    "spread_bps": "2",
                    "impact_bps_at_max_participation": "5",
                    "daily_volatility_fraction": "0.01",
                    "stale_market_data": False,
                }
            ],
        },
    )
    assert liquidity.status_code == 200
    assert liquidity.json()["allowed"] is True
    assert liquidity.json()["execution_authority_granted"] is False

    counterparty = api.post(
        "/api/v1/convergence/treasury/counterparty/evaluate",
        json={
            "exposures": [
                {
                    "counterparty_id": "broker-api",
                    "receivable": "100",
                    "payable": "20",
                    "posted_collateral": "50",
                    "collateral_haircut_fraction": "0.1",
                    "wrong_way_risk_multiplier": "1",
                    "credit_limit": "1000",
                    "overdue_receivable": "0",
                    "disputed_amount": "0",
                    "netting_set_id": "set-api",
                }
            ],
            "maximum_total_exposure": "1000",
        },
    )
    assert counterparty.status_code == 200
    assert counterparty.json()["allowed"] is True
    assert counterparty.json()["settlement_authority_granted"] is False

    change = api.post(
        "/api/v1/convergence/operations/change-control/evaluate",
        json={
            "change_id": "change-api-0001",
            "evaluated_at": NOW,
            "starts_at": "2026-07-27T13:00:00Z",
            "ends_at": "2026-07-27T14:00:00Z",
            "risk": "medium",
            "affected_components": ["api"],
            "rollback_steps": ["restore previous release"],
            "canary_fraction": 0.05,
            "observations": [
                {
                    "metric": "success_rate",
                    "baseline": 0.99,
                    "canary": 0.99,
                    "maximum_relative_degradation": 0.01,
                    "higher_is_better": True,
                }
            ],
            "freeze_active": False,
            "tested_rollback": True,
        },
    )
    assert change.status_code == 200
    assert change.json()["ready_for_governance_review"] is True
    assert change.json()["deployment_authority_granted"] is False

    sequential = api.post(
        "/api/v1/convergence/experiments/sequential/evaluate",
        json={
            "experiment_id": "experiment-api-01",
            "looks": [
                {
                    "look": 1,
                    "information_fraction": 1.0,
                    "z_score": 3.0,
                    "sample_size": 100,
                }
            ],
            "hypotheses": [{"hypothesis_id": "alpha", "p_value": 0.001}],
            "maximum_looks": 1,
        },
    )
    assert sequential.status_code == 200
    assert sequential.json()["ready_for_human_review"] is True
    assert sequential.json()["promotion_authority_granted"] is False


def test_schema_treasury_factor_model_forensics_controls_and_runbooks_api(
    tmp_path, monkeypatch
):
    api = client(tmp_path, monkeypatch)
    schema = api.post(
        "/api/v1/convergence/contracts/schemas/compatibility",
        json={
            "current": {
                "contract_id": "orders.event",
                "version": 1,
                "fields": [
                    {
                        "name": "event_id",
                        "data_type": "string",
                        "required": True,
                        "nullable": False,
                        "semantic": "event identity",
                    },
                    {
                        "name": "occurred_at",
                        "data_type": "datetime",
                        "required": True,
                        "nullable": False,
                        "semantic": "event time",
                    },
                ],
                "primary_key": ["event_id"],
                "event_time_field": "occurred_at",
            },
            "candidate": {
                "contract_id": "orders.event",
                "version": 2,
                "fields": [
                    {
                        "name": "event_id",
                        "data_type": "string",
                        "required": True,
                        "nullable": False,
                        "semantic": "event identity",
                    },
                    {
                        "name": "occurred_at",
                        "data_type": "datetime",
                        "required": True,
                        "nullable": False,
                        "semantic": "event time",
                    },
                    {
                        "name": "trace_id",
                        "data_type": "string",
                        "required": False,
                        "nullable": True,
                        "semantic": "trace identity",
                    },
                ],
                "primary_key": ["event_id"],
                "event_time_field": "occurred_at",
            },
            "mode": "full",
        },
    )
    assert schema.status_code == 200, schema.text
    assert schema.json()["compatible"] is True
    assert schema.json()["registration_authority_granted"] is False

    treasury = api.post(
        "/api/v1/convergence/treasury/liquidity/forecast",
        json={
            "evaluated_at": NOW,
            "reporting_currency": "GBP",
            "balances": [{"currency": "GBP", "available": "1000", "restricted": "0"}],
            "cashflows": [
                {
                    "cashflow_id": "fee-api",
                    "value_date": "2026-07-28",
                    "currency": "GBP",
                    "amount": "-100",
                    "kind": "fee",
                    "confidence": "committed",
                    "source_sha256": "a" * 64,
                }
            ],
            "horizon_days": [1, 7],
            "minimum_reporting_buffer": "500",
        },
    )
    assert treasury.status_code == 200, treasury.text
    assert treasury.json()["ready_for_treasury_review"] is True
    assert treasury.json()["transfer_authority_granted"] is False

    factor = api.post(
        "/api/v1/convergence/risk/factors/evaluate",
        json={
            "portfolio_equity": 1000,
            "positions": [
                {
                    "position_id": "p1",
                    "market_value": 500,
                    "factor_loadings": {"market": 1.0},
                }
            ],
            "covariance": {"factors": ["market"], "matrix": [[0.04]]},
            "maximum_factor_exposure": {"market": 1.0},
            "maximum_factor_volatility": 0.2,
        },
    )
    assert factor.status_code == 200, factor.text
    assert factor.json()["allowed"] is True
    assert factor.json()["hedge_authority_granted"] is False

    model = api.post(
        "/api/v1/convergence/experiments/model-risk/evaluate",
        json={
            "model_id": "model-api",
            "model_sha256": "b" * 64,
            "runs": [
                {
                    "run_id": "r1",
                    "seed": 1,
                    "regime": "trend",
                    "annual_return": 0.1,
                    "sharpe": 1.0,
                    "maximum_drawdown": 0.1,
                    "turnover": 1.0,
                    "baseline_annual_return": 0.05,
                },
                {
                    "run_id": "r2",
                    "seed": 2,
                    "regime": "trend",
                    "annual_return": 0.11,
                    "sharpe": 1.1,
                    "maximum_drawdown": 0.1,
                    "turnover": 1.0,
                    "baseline_annual_return": 0.05,
                },
                {
                    "run_id": "r3",
                    "seed": 3,
                    "regime": "range",
                    "annual_return": 0.08,
                    "sharpe": 0.8,
                    "maximum_drawdown": 0.12,
                    "turnover": 1.0,
                    "baseline_annual_return": 0.04,
                },
            ],
            "minimum_median_excess_return": 0.01,
            "maximum_return_dispersion": 0.1,
            "maximum_drawdown": 0.2,
        },
    )
    assert model.status_code == 200, model.text
    assert model.json()["ready_for_model_risk_review"] is True
    assert model.json()["promotion_authority_granted"] is False

    evidence_payload = {
        "evidence_id": "e-api",
        "source_id": "audit",
        "collected_at": "2026-07-27T12:00:00+00:00",
        "content_sha256": "c" * 64,
        "size_bytes": 10,
        "classification": "internal",
        "immutable": True,
        "redacted": True,
        "previous_receipt_sha256": None,
    }
    receipt = hashlib.sha256(
        json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    forensics = api.post(
        "/api/v1/convergence/operations/forensics/evaluate",
        json={
            "incident_id": "inc-api",
            "evaluated_at": NOW,
            "evidence": [{**evidence_payload, "receipt_sha256": receipt}],
            "required_source_ids": ["audit"],
        },
    )
    assert forensics.status_code == 200, forensics.text
    assert forensics.json()["ready_for_forensic_review"] is True
    assert forensics.json()["export_authority_granted"] is False

    controls = api.post(
        "/api/v1/convergence/governance/controls/evaluate",
        json={
            "evaluated_at": NOW,
            "controls": [
                {
                    "control_id": "c-api",
                    "objective": "Separate approval",
                    "risk_id": "r-api",
                    "owner_role": "governance",
                    "frequency": "event",
                    "automated": True,
                    "preventive": True,
                }
            ],
            "tests": [
                {
                    "test_id": "t-api",
                    "control_id": "c-api",
                    "tested_at": NOW,
                    "tester_role": "audit",
                    "sample_size": 10,
                    "exceptions": 0,
                    "result": "pass",
                    "evidence_sha256": "d" * 64,
                }
            ],
            "required_risk_ids": ["r-api"],
        },
    )
    assert controls.status_code == 200, controls.text
    assert controls.json()["ready_for_governance_review"] is True
    assert controls.json()["remediation_authority_granted"] is False

    impact = api.post(
        "/api/v1/convergence/operations/dependencies/impact",
        json={
            "change_id": "change-api",
            "services": [
                {
                    "service_id": "db",
                    "criticality": "high",
                    "dependencies": [],
                    "capacity_units": 100,
                    "observed_load_units": 50,
                    "rollback_available": True,
                    "owner_role": "data",
                },
                {
                    "service_id": "api",
                    "criticality": "medium",
                    "dependencies": ["db"],
                    "capacity_units": 100,
                    "observed_load_units": 50,
                    "rollback_available": True,
                    "owner_role": "platform",
                },
            ],
            "directly_changed_services": ["db"],
            "maximum_critical_blast_radius": 0,
        },
    )
    assert impact.status_code == 200, impact.text
    assert impact.json()["ready_for_change_review"] is True
    assert impact.json()["deployment_authority_granted"] is False

    runbook = api.post(
        "/api/v1/convergence/operations/runbooks/evaluate",
        json={
            "runbook_id": "rb-api",
            "version": 1,
            "risk_level": "high",
            "evaluated_at": NOW,
            "last_rehearsed_at": "2026-07-26T12:00:00Z",
            "required_roles": ["operator", "reviewer"],
            "steps": [
                {
                    "step_id": "change",
                    "order": 1,
                    "kind": "manual_change",
                    "owner_role": "operator",
                    "instruction": "Apply approved change",
                    "precondition": "Approval exists",
                    "success_evidence": "Change receipt",
                    "timeout_seconds": 60,
                    "destructive": True,
                    "rollback_step_id": "rollback",
                },
                {
                    "step_id": "verify",
                    "order": 2,
                    "kind": "verification",
                    "owner_role": "reviewer",
                    "instruction": "Verify state",
                    "precondition": "Change complete",
                    "success_evidence": "Health report",
                    "timeout_seconds": 60,
                },
                {
                    "step_id": "rollback",
                    "order": 3,
                    "kind": "rollback",
                    "owner_role": "operator",
                    "instruction": "Restore previous version",
                    "precondition": "Rollback approved",
                    "success_evidence": "Restored health",
                    "timeout_seconds": 60,
                },
            ],
        },
    )
    assert runbook.status_code == 200, runbook.text
    assert runbook.json()["ready_for_operational_review"] is True
    assert runbook.json()["execution_authority_granted"] is False
