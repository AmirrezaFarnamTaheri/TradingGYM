"""REST surface for converged strategy, research, market, risk, and governance planes."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from trading_gym.core.legacy_api.runtime import public_error_detail
from trading_gym.core.coordination import LeaseAcquire, LeaseConflict, LeaseRepository
from trading_gym.core.budgets import (
    BudgetConflict,
    BudgetCreate,
    BudgetRepository,
    BudgetReservationCreate,
)
from trading_gym.execution.accounting import (
    CorporateActionRequest,
    AccountingEvent,
    AccountingRepository,
    evaluate_settlement,
    plan_corporate_action,
    value_account,
)
from trading_gym.agent.providers import (
    ProviderRepository,
    provider_status,
)
from trading_gym.core.audit_outbox import AuditOutboxPending
from trading_gym.agent.tools import SafeToolRegistry, ToolRequest
from trading_gym.core.control_planes import BacktestWorkflowRequest, ConvergenceWorkflowService
from trading_gym.core.control_planes.budgeted_backtest import (
    BudgetedBacktestCompensationError,
    BudgetedBacktestRequest,
    BudgetedBacktestWorkflow,
)
from trading_gym.core.control_planes.order_accounting import OrderAccountingSyncService
from trading_gym.execution.calibration import (
    calibrate_execution,
)
from trading_gym.rl.experiments import (
    ExperimentConflict,
    ExperimentCreate,
    ExperimentRepository,
    ExperimentState,
    RunManifestCreate,
    RunManifestRepository,
    verify_manifest,
)
from trading_gym.execution.governance.identity import (
    ActorAuthenticationError,
    ActorIdentityConfigurationError,
    ActorValidationError,
    require_actor_identity,
)
from trading_gym.execution.governance.models import (
    ConsumeRequest,
    DecisionRequest,
    ProposalCreate,
    ProposalState,
)
from trading_gym.execution.governance.service import (
    GovernanceAuditPending,
    GovernanceConflict,
    GovernanceService,
)
from trading_gym.execution.governance.triggers import (
    TriggerConflict,
    TriggerCreate,
    TriggerRepository,
    TriggerService,
    TriggerState,
)
from trading_gym.data.lineage import (
    LineageConflict,
    LineageNodeKind,
    LineageRepository,
)
from trading_gym.market import (
    BacktestLimitError,
    ExecutionWindowRequest,
    inspect_market_session,
    plan_execution_window,
    DatasetCatalog,
    DatasetConflict,
    DatasetState,
    InstrumentConflict,
    InstrumentRegistry,
    validate_backtest_window,
)
from trading_gym.core.monitoring.drift import (
    DriftBaselineRepository,
    DriftEvaluationRequest,
    evaluate_drift,
)
from trading_gym.core.operations import (
    IncidentConflict,
    IncidentCreate,
    IncidentRepository,
    MigrationPlanRequest,
    evaluate_incident_readiness,
    plan_migration,
    verify_attestation,
    SnapshotService,
)
from trading_gym.core.supply_chain import verify_dependency_policy
from trading_gym.execution.orders import (
    OrderConflict,
    OrderIntent,
    OrderRepository,
    OrderState,
)
from trading_gym.rl.performance import analyse_lots
from trading_gym.execution.portfolio import (
    plan_rebalance,
)
from trading_gym.agent.research.debate import debate_prompt_pack
from trading_gym.agent.research.dossier import build_dossier
from trading_gym.agent.research.quality import assess_dossier
from trading_gym.agent.research.sources import source_catalogue
from trading_gym.agent.research.workspace import (
    ResearchWorkspace,
    WorkspaceConflict,
)
from trading_gym.execution.risk.derivatives import (
    analyze_derivative_risk,
)
from trading_gym.execution.risk.stress import (
    evaluate_portfolio_stress,
)
from trading_gym.agent.strategy_lab.repository import (
    StrategyConflictError,
    StrategyRepository,
)
from trading_gym.agent.strategy_lab.templates import template_catalogue
from trading_gym.agent.strategy_lab.validation import validate_strategy_spec


from trading_gym.core.legacy_api.convergence_evaluations import create_evaluation_router
from trading_gym.core.legacy_api.convergence_models import (
    StrategySaveRequest as StrategySaveRequest,
    StrategyValidateRequest as StrategyValidateRequest,
    DossierRequest as DossierRequest,
    ReflectionRequest as ReflectionRequest,
    PortfolioStressRequest as PortfolioStressRequest,
    ProviderSaveRequest as ProviderSaveRequest,
    InstrumentSaveRequest as InstrumentSaveRequest,
    LeaseRenewRequest as LeaseRenewRequest,
    LeaseReleaseRequest as LeaseReleaseRequest,
    EventReplayRequest as EventReplayRequest,
    BacktestWindowRequest as BacktestWindowRequest,
    DatasetSaveRequest as DatasetSaveRequest,
    DatasetQuarantineRequest as DatasetQuarantineRequest,
    WorkspaceWatchlistRequest as WorkspaceWatchlistRequest,
    WorkspaceNoteRequest as WorkspaceNoteRequest,
    WorkspaceReportRequest as WorkspaceReportRequest,
    ExperimentClaimRequest as ExperimentClaimRequest,
    ExperimentHeartbeatRequest as ExperimentHeartbeatRequest,
    ExperimentCompleteRequest as ExperimentCompleteRequest,
    ExperimentFailRequest as ExperimentFailRequest,
    ExperimentCancelRequest as ExperimentCancelRequest,
    AccountCreateRequest as AccountCreateRequest,
    AccountValuationRequest as AccountValuationRequest,
    AccountReconciliationRequest as AccountReconciliationRequest,
    ExecutionSimulationEnvelope as ExecutionSimulationEnvelope,
    DriftBaselineSaveRequest as DriftBaselineSaveRequest,
    SnapshotCreateRequest as SnapshotCreateRequest,
    SLOEvaluationRequest as SLOEvaluationRequest,
    OrderAppendRequest as OrderAppendRequest,
    OrderReconciliationRequestEnvelope as OrderReconciliationRequestEnvelope,
    LotAnalysisRequest as LotAnalysisRequest,
    ExecutionCalibrationRequest as ExecutionCalibrationRequest,
    LineageNodeSaveRequest as LineageNodeSaveRequest,
    LineageEdgeSaveRequest as LineageEdgeSaveRequest,
    BudgetCommitRequest as BudgetCommitRequest,
    BudgetReleaseRequest as BudgetReleaseRequest,
    AttestationVerifyRequest as AttestationVerifyRequest,
    MarketSessionInspectionRequest as MarketSessionInspectionRequest,
    DerivativeRiskRequest as DerivativeRiskRequest,
    RebalanceRequest as RebalanceRequest,
    SettlementEvaluationRequest as SettlementEvaluationRequest,
    IncidentAppendRequest as IncidentAppendRequest,
    IncidentReadinessRequest as IncidentReadinessRequest,
    TriggerStateRequest as TriggerStateRequest,
    TriggerEvaluationRequest as TriggerEvaluationRequest,
)


def create_convergence_router(auth_dependency: Callable[..., Any]) -> APIRouter:
    router = APIRouter(
        prefix="/convergence",
        tags=["convergence"],
        dependencies=[Depends(auth_dependency)],
    )
    governance = GovernanceService()
    leases = LeaseRepository(ledger=governance.ledger)
    strategies = StrategyRepository(ledger=governance.ledger)
    providers = ProviderRepository(ledger=governance.ledger)
    instruments = InstrumentRegistry(ledger=governance.ledger)
    datasets = DatasetCatalog(ledger=governance.ledger)
    experiments = ExperimentRepository(ledger=governance.ledger)
    manifests = RunManifestRepository(ledger=governance.ledger)
    accounting = AccountingRepository(ledger=governance.ledger)
    orders = OrderRepository(ledger=governance.ledger)
    lineage = LineageRepository(ledger=governance.ledger)
    budgets = BudgetRepository(ledger=governance.ledger)
    incidents = IncidentRepository(ledger=governance.ledger)
    order_accounting_sync = OrderAccountingSyncService(
        orders=orders, accounting=accounting, ledger=governance.ledger
    )
    drift_baselines = DriftBaselineRepository(ledger=governance.ledger)
    snapshots = SnapshotService(ledger=governance.ledger)
    workspace = ResearchWorkspace(ledger=governance.ledger)
    triggers = TriggerRepository(ledger=governance.ledger)
    trigger_service = TriggerService(triggers, governance)
    workflows = ConvergenceWorkflowService(
        strategies=strategies,
        instruments=instruments,
        experiments=experiments,
        manifests=manifests,
        ledger=governance.ledger,
    )
    budgeted_workflows = BudgetedBacktestWorkflow(
        base=workflows, budgets=budgets, lineage=lineage
    )
    tools = SafeToolRegistry(
        strategies=strategies,
        governance=governance,
        instruments=instruments,
        experiments=experiments,
        workspace=workspace,
        workflows=workflows,
    )
    audit_stores: dict[str, Any] = {
        "governance": governance,
        "triggers": triggers,
        "strategies": strategies,
        "providers": providers,
        "instruments": instruments,
        "datasets": datasets,
        "experiments": experiments,
        "run_manifests": manifests,
        "accounting": accounting,
        "orders": orders,
        "lineage": lineage,
        "budgets": budgets,
        "incidents": incidents,
        "order_accounting_sync": order_accounting_sync,
        "drift_baselines": drift_baselines,
        "snapshots": snapshots,
        "research_workspace": workspace,
        "coordination_leases": leases,
    }

    def reconcile_all_audit(*, fail_closed: bool) -> dict[str, Any]:
        statuses: dict[str, Any] = {}
        errors: dict[str, str] = {}
        total_delivered = 0
        total_pending = 0
        for name, store in audit_stores.items():
            try:
                status = store.reconcile_audit()
                delivered = int(status.get("delivered", 0))
                pending = int(status.get("pending", 0))
                statuses[name] = {"delivered": delivered, "pending": pending}
                total_delivered += delivered
                total_pending += pending
            except (
                AuditOutboxPending,
                GovernanceAuditPending,
                OSError,
                RuntimeError,
                ValueError,
            ) as exc:
                pending = (
                    int(store.audit_pending())
                    if hasattr(store, "audit_pending")
                    else int(store.audit_health(reconcile=False)["pending"])
                )
                statuses[name] = {"delivered": 0, "pending": pending, "degraded": True}
                errors[name] = str(exc)
                total_pending += pending
        result = {
            "delivered": total_delivered,
            "pending": total_pending,
            "stores": statuses,
            "ledger": governance.ledger.verify(),
        }
        if errors:
            result["degraded_stores"] = sorted(errors)
            if fail_closed:
                raise HTTPException(
                    status_code=503,
                    detail="One or more durable audit outboxes could not be reconciled",
                )
        return result

    def worker_authorised(supplied: str | None) -> None:
        expected = os.getenv("WORKER_API_KEY") or ""
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="WORKER_API_KEY is required for worker transitions",
            )
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=403, detail="Valid X-Worker-Key required")

    def actor_authorised(
        actor: str,
        supplied_actor_key: str | None,
        *,
        required_role: str,
    ) -> None:
        try:
            require_actor_identity(
                actor.strip(),
                supplied_actor_key,
                required_roles={required_role},
            )
        except ActorValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ActorAuthenticationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ActorIdentityConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    def proposal_actor_authorised(
        proposal_id: str,
        actor: str,
        supplied_actor_key: str | None,
        *,
        required_role: str,
    ) -> Any:
        try:
            proposal = governance.get(proposal_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Proposal not found") from exc
        if governance.requires_separate_approval_key(proposal.risk_tier):
            actor_authorised(
                actor,
                supplied_actor_key,
                required_role=required_role,
            )
        return proposal

    def approval_authorised(
        proposal_id: str,
        supplied: str | None,
        *,
        actor: str,
        actor_key: str | None,
        required_role: str,
    ) -> None:
        proposal = proposal_actor_authorised(
            proposal_id,
            actor,
            actor_key,
            required_role=required_role,
        )
        if not governance.requires_separate_approval_key(proposal.risk_tier):
            return
        expected = os.getenv("APPROVAL_API_KEY") or ""
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="APPROVAL_API_KEY is required for high-risk operations",
            )
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=403,
                detail="Valid X-Approval-Key required for high-risk operation",
            )
        health = reconcile_all_audit(fail_closed=True)
        audit = health["ledger"]
        if health["pending"] or not audit.get("valid") or not audit.get("sealed"):
            raise HTTPException(
                status_code=503,
                detail=(
                    "High-risk approval requires zero pending audit events and a valid "
                    "HMAC-sealed ledger"
                ),
            )

    @router.get("/capabilities")
    def capabilities() -> dict[str, Any]:
        audit_health = reconcile_all_audit(fail_closed=False)
        audit = audit_health["ledger"]
        return {
            "strategy_lab": {
                "declarative_only": True,
                "templates": list(template_catalogue()),
                "workload_policy": True,
                "overfitting_controls": True,
            },
            "research": {
                "evidence_first": True,
                "source_count": len(source_catalogue()),
                "quality_assessment": True,
                "durable_workspace": True,
            },
            "market": {
                "canonical_instruments": True,
                "provider_failover_contract": True,
                "durable_dataset_catalogue": True,
                "bounded_backtests": True,
                "timezone_safe_session_calendar": True,
                "holiday_early_close_and_maintenance_gates": True,
            },
            "risk": {
                "position_sizing": True,
                "portfolio_gates": True,
                "multi_asset_stress": True,
                "derivative_margin_funding_and_liquidation_analysis": True,
                "existing_execution_risk_controls_preserved": True,
            },
            "workflows": {
                "strategy_to_backtest_plan": True,
                "budgeted_lineage_backtest_plan": True,
                "execution_authority_granted": False,
            },
            "experiments": {
                "durable_jobs": True,
                "leases": True,
                "heartbeats": True,
                "reconciliation": True,
                "immutable_run_manifests": True,
                "source_and_seed_fingerprints": True,
                "point_in_time_leakage_audit": True,
            },
            "orders": {
                "replay_verified_lifecycle": True,
                "broker_reconciliation_is_non_mutating": True,
                "multi_currency_valuation": True,
                "recoverable_accounting_sync": True,
                "venue_execution_authority": False,
            },
            "accounting": {
                "decimal_event_ledger": True,
                "materialized_state_replay_verified": True,
                "broker_reconciliation_is_non_mutating": True,
                "multi_currency_valuation": True,
                "calendar_aware_settlement_projection": True,
                "recoverable_accounting_sync": True,
                "corporate_action_planning": True,
                "corporate_action_authority_granted": False,
            },
            "portfolio_planning": {
                "constraint_aware_rebalance": True,
                "cash_turnover_and_increment_gates": True,
                "execution_authority_granted": False,
            },
            "performance": {
                "fifo_lifo_hifo_lots": True,
                "explicit_fill_time_fx": True,
                "regulatory_authority": False,
            },
            "lineage": {
                "content_addressed_dag": True,
                "impact_and_ancestry": True,
                "authority_granted": False,
            },
            "budgets": {
                "atomic_reservations": True,
                "hard_and_soft_limits": True,
                "execution_authority_granted": False,
            },
            "execution_research": {
                "deterministic_partial_fill_simulation": True,
                "adverse_scenarios": True,
                "queue_position_simulation": True,
                "observed_fill_calibration": True,
                "execution_authority_granted": False,
            },
            "monitoring": {
                "persisted_drift_baselines": True,
                "slo_error_budgets": True,
            },
            "operations": {
                "verified_local_snapshots": True,
                "replay_verified_incident_lifecycle": True,
                "incident_roles_actions_and_postmortems": True,
                "restore_is_plan_only": True,
                "isolated_restore_drills": True,
                "local_hmac_attestation_verification": True,
                "dependency_inventory": True,
                "dry_run_migration_plans": True,
                "version_skew_evaluation": True,
                "multi_source_clock_health": True,
                "rpo_rto_evidence": True,
                "recovery_authority_granted": False,
            },
            "assurance": {
                "cross_plane_qualification": True,
                "system_qualification": True,
                "human_review_only": True,
                "promotion_authority_granted": False,
            },
            "governance": {
                "proposal_only_automation": True,
                "audit_mode": audit.get("mode", "chain_only"),
                "audit_sealed": bool(audit.get("sealed")),
                "audit_pending": int(audit_health["pending"]),
                "persistent_triggers": True,
                "bounded_recurrence_planning": True,
                "principal_bound_actor_identity": True,
                "identity_readiness": True,
                "single_tenant_boundary": True,
                "multi_tenant_supported": False,
                "data_retention_and_legal_hold_planning": True,
                "credential_rotation_readiness": True,
                "destructive_authority_granted": False,
            },
            "coordination": {
                "leases_with_expiry": True,
                "monotonic_fencing_tokens": True,
                "execution_authority_granted": False,
            },
            "event_replay": {
                "hash_chain_verification": True,
                "sequence_and_duplicate_detection": True,
                "mutation_authority_granted": False,
            },
            "treasury": {
                "haircut_and_liquidity_aware_collateral": True,
                "multi_currency_liquidity_ladder": True,
                "transfer_authority_granted": False,
            },
            "schema_assurance": {
                "versioned_contract_compatibility": True,
                "registration_or_migration_authority": False,
            },
            "factor_risk": {
                "psd_covariance_and_risk_contributions": True,
                "hedge_or_execution_authority": False,
            },
            "model_risk": {
                "seed_and_regime_challenge_testing": True,
                "promotion_authority_granted": False,
            },
            "forensics": {
                "chain_of_custody_and_timeline_readiness": True,
                "export_or_legal_authority": False,
            },
            "control_effectiveness": {
                "independent_current_evidence": True,
                "remediation_authority_granted": False,
            },
            "dependency_impact": {
                "transitive_blast_radius_and_headroom": True,
                "deployment_authority_granted": False,
            },
            "runbooks": {
                "rehearsal_rollback_and_role_separation": True,
                "execution_authority_granted": False,
            },
            "policy_assurance": {
                "version_hash_lineage": True,
                "decision_impact_analysis": True,
                "activation_authority_granted": False,
            },
            "compliance_evidence": {
                "best_execution_surveillance": True,
                "potential_self_trade_detection": True,
                "legal_or_enforcement_authority": False,
            },
            "privacy": {
                "minimisation_and_subject_request_planning": True,
                "deletion_authority_granted": False,
            },
            "capacity": {
                "worker_load_and_backpressure_planning": True,
                "scaling_authority_granted": False,
            },
            "failover": {
                "freshness_quorum_and_divergence_checks": True,
                "failover_authority_granted": False,
            },
            "liquidity_risk": {
                "exit_horizon_and_cost_analysis": True,
                "execution_authority_granted": False,
            },
            "counterparty_risk": {
                "netting_collateral_and_wrong_way_risk": True,
                "settlement_authority_granted": False,
            },
            "change_control": {
                "canary_rollback_and_freeze_evidence": True,
                "deployment_authority_granted": False,
            },
            "agent": {
                "tools": tools.describe(),
                "direct_live_trade_tool": False,
                "arbitrary_code_tool": False,
            },
        }

    @router.get("/strategies/templates")
    def strategy_templates() -> dict[str, Any]:
        return {
            key: value.model_dump(mode="json")
            for key, value in template_catalogue().items()
        }

    @router.post("/strategies/validate")
    def validate_strategy(request: StrategyValidateRequest) -> dict[str, Any]:
        try:
            return validate_strategy_spec(
                request.spec,
                market=request.market,
                start=request.start,
                end=request.end,
            ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/strategies")
    def list_strategies(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return strategies.list()[offset : offset + limit]

    @router.get("/strategies/{strategy_id}")
    def get_strategy(strategy_id: str) -> dict[str, Any]:
        try:
            return strategies.get(strategy_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Strategy not found") from exc

    @router.post("/strategies")
    def save_strategy(request: StrategySaveRequest) -> dict[str, Any]:
        try:
            return strategies.save(
                request.spec,
                strategy_id=request.strategy_id,
                expected_revision=request.expected_revision,
                actor=request.actor,
            )
        except StrategyConflictError as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/market/session/inspect")
    def inspect_market_session_endpoint(
        request: MarketSessionInspectionRequest,
    ) -> dict[str, Any]:
        try:
            return inspect_market_session(
                request.calendar, request.observed_at
            ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/market/execution-window")
    def plan_market_execution_window(request: ExecutionWindowRequest) -> dict[str, Any]:
        try:
            return plan_execution_window(request).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/market/instruments")
    def list_instruments(
        active_only: bool = Query(default=False),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        values = instruments.list(active_only=active_only)
        return {
            "revision": instruments.revision(),
            "instruments": [
                item.model_dump(mode="json") for item in values[offset : offset + limit]
            ],
        }

    @router.get("/market/instruments/resolve/{identifier}")
    def resolve_instrument(
        identifier: str, venue: str | None = Query(default=None)
    ) -> dict[str, Any]:
        try:
            return instruments.resolve(identifier, venue=venue).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Instrument not found or ambiguous"
            ) from exc

    @router.post("/market/instruments")
    def save_instrument(
        request: InstrumentSaveRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return instruments.save(
                request.instrument,
                expected_revision=request.expected_revision,
                actor=actor,
            )
        except InstrumentConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/market/backtest-policy")
    def backtest_policy(request: BacktestWindowRequest) -> dict[str, object]:
        try:
            return validate_backtest_window(**request.model_dump())
        except BacktestLimitError as exc:
            raise HTTPException(status_code=422, detail=exc.details) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/market/datasets")
    def list_datasets(
        instrument_id: str | None = Query(default=None),
        timeframe: str | None = Query(default=None),
        state: DatasetState | None = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        records = datasets.list(
            instrument_id=instrument_id,
            timeframe=timeframe,
            state=state,
        )
        return {
            "revision": datasets.revision(),
            "datasets": [
                item.model_dump(mode="json")
                for item in records[offset : offset + limit]
            ],
        }

    @router.post("/market/datasets")
    def register_dataset(
        request: DatasetSaveRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return datasets.register(
                request.dataset,
                expected_catalog_revision=request.expected_catalog_revision,
                actor=actor,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Superseded dataset not found"
            ) from exc
        except DatasetConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.get("/market/datasets/{dataset_id}")
    def get_dataset(dataset_id: str) -> dict[str, Any]:
        try:
            return datasets.get(dataset_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Dataset not found") from exc

    @router.post("/market/datasets/{dataset_id}/quarantine")
    def quarantine_dataset(
        dataset_id: str,
        request: DatasetQuarantineRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return datasets.quarantine(
                dataset_id, actor=actor, **request.model_dump()
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Dataset not found") from exc
        except DatasetConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/workflows/backtest")
    def plan_backtest_workflow(
        request: BacktestWorkflowRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return workflows.plan_backtest(request, actor=actor).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Strategy or instrument not found"
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/workflows/budgeted-backtest")
    def plan_budgeted_backtest_workflow(
        request: BudgetedBacktestRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return budgeted_workflows.plan(request, actor=actor).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Strategy, instrument, or budget not found"
            ) from exc
        except (BudgetConflict, LineageConflict, ExperimentConflict) as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except BudgetedBacktestCompensationError as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        except (ValueError, AuditOutboxPending) as exc:
            raise HTTPException(
                status_code=422 if isinstance(exc, ValueError) else 503,
                detail=public_error_detail(exc),
            ) from exc

    @router.post("/risk/derivatives")
    def assess_derivative_risk(request: DerivativeRiskRequest) -> dict[str, Any]:
        try:
            return analyze_derivative_risk(
                equity=request.equity,
                free_collateral=request.free_collateral,
                positions=request.positions,
                policy=request.policy,
            ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/portfolio/rebalance")
    def plan_portfolio_rebalance(request: RebalanceRequest) -> dict[str, Any]:
        try:
            return plan_rebalance(
                base_currency=request.base_currency,
                cash_base=request.cash_base,
                holdings=request.holdings,
                targets=request.targets,
                policy=request.policy,
            ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/risk/stress")
    def stress_portfolio(request: PortfolioStressRequest) -> dict[str, Any]:
        try:
            return evaluate_portfolio_stress(
                equity=request.equity,
                positions=request.positions,
                scenario=request.scenario,
                policy=request.policy,
            ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/research/dossier")
    def create_dossier(request: DossierRequest) -> dict[str, Any]:
        required = (
            tuple(request.required_sections) if request.required_sections else None
        )
        kwargs: dict[str, Any] = {
            "subject": request.subject,
            "evidence": request.evidence,
        }
        if required is not None:
            kwargs["required_sections"] = required
        dossier = build_dossier(**kwargs)
        return {
            "dossier": dossier.model_dump(mode="json"),
            "quality": assess_dossier(dossier).model_dump(mode="json"),
            "debate_prompts": debate_prompt_pack(dossier),
        }

    @router.get("/research/workspace")
    def research_workspace() -> dict[str, Any]:
        return workspace.snapshot()

    @router.post("/research/workspace/watchlist")
    def save_watchlist(
        request: WorkspaceWatchlistRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return workspace.save_watchlist(
                request.entry, expected_revision=request.expected_revision, actor=actor
            )
        except WorkspaceConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.delete("/research/workspace/watchlist/{instrument_id}")
    def remove_watchlist(
        instrument_id: str,
        expected_revision: int = Query(ge=0),
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, int]:
        try:
            return {
                "revision": workspace.remove_watchlist(
                    instrument_id, expected_revision=expected_revision, actor=actor
                )
            }
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Watchlist entry not found"
            ) from exc
        except WorkspaceConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/research/workspace/notes")
    def save_note(
        request: WorkspaceNoteRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return workspace.save_note(actor=actor, **request.model_dump()).model_dump(
                mode="json"
            )
        except WorkspaceConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/research/workspace/reports")
    def save_report(
        request: WorkspaceReportRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return workspace.save_report(
                request.dossier, label=request.label, actor=actor
            )
        except WorkspaceConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.get("/providers")
    def list_providers(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        values = providers.list()
        return {
            "revision": providers.revision(),
            "providers": [
                item.model_dump(mode="json") for item in values[offset : offset + limit]
            ],
        }

    @router.post("/providers")
    def save_provider(
        request: ProviderSaveRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return providers.save(
                request.config, expected_revision=request.expected_revision, actor=actor
            )
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc

    @router.delete("/providers/{provider_id}")
    def delete_provider(
        provider_id: str,
        expected_revision: int | None = Query(default=None, ge=0),
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return providers.delete(
                provider_id, expected_revision=expected_revision, actor=actor
            )
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc

    @router.get("/providers/{provider_id}/status")
    def check_provider(provider_id: str) -> dict[str, Any]:
        config = next(
            (item for item in providers.list() if item.provider_id == provider_id), None
        )
        if config is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        try:
            return provider_status(config)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/experiments")
    def list_experiments(
        state: ExperimentState | None = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        try:
            values = experiments.list(state=state)
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        return [
            item.model_dump(mode="json") for item in values[offset : offset + limit]
        ]

    @router.post("/experiments")
    def create_experiment(
        request: ExperimentCreate,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return experiments.create(request, actor=actor).model_dump(mode="json")
        except ExperimentConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.get("/experiments/{experiment_id}")
    def get_experiment(experiment_id: str) -> dict[str, Any]:
        try:
            return experiments.get(experiment_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Experiment not found") from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/experiments/claim")
    def claim_experiment(
        request: ExperimentClaimRequest,
        worker_key: str | None = Header(default=None, alias="X-Worker-Key"),
    ) -> dict[str, Any] | None:
        worker_authorised(worker_key)
        try:
            record = experiments.claim_next(**request.model_dump())
            return record.model_dump(mode="json") if record else None
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/experiments/{experiment_id}/heartbeat")
    def heartbeat_experiment(
        experiment_id: str,
        request: ExperimentHeartbeatRequest,
        worker_key: str | None = Header(default=None, alias="X-Worker-Key"),
    ) -> dict[str, Any]:
        worker_authorised(worker_key)
        try:
            return experiments.heartbeat(
                experiment_id, **request.model_dump()
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Experiment not found") from exc
        except ExperimentConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc

    @router.post("/experiments/{experiment_id}/complete")
    def complete_experiment(
        experiment_id: str,
        request: ExperimentCompleteRequest,
        worker_key: str | None = Header(default=None, alias="X-Worker-Key"),
    ) -> dict[str, Any]:
        worker_authorised(worker_key)
        try:
            return experiments.complete(
                experiment_id, **request.model_dump()
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Experiment not found") from exc
        except ExperimentConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/experiments/{experiment_id}/fail")
    def fail_experiment(
        experiment_id: str,
        request: ExperimentFailRequest,
        worker_key: str | None = Header(default=None, alias="X-Worker-Key"),
    ) -> dict[str, Any]:
        worker_authorised(worker_key)
        try:
            return experiments.fail(experiment_id, **request.model_dump()).model_dump(
                mode="json"
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Experiment not found") from exc
        except ExperimentConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/experiments/{experiment_id}/cancel")
    def cancel_experiment(
        experiment_id: str,
        request: ExperimentCancelRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return experiments.cancel(
                experiment_id, actor=actor, **request.model_dump()
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Experiment not found") from exc
        except ExperimentConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.get("/agent/tools")
    def list_tools() -> list[dict[str, str]]:
        return tools.describe()

    @router.post("/agent/tools/invoke")
    def invoke_tool(request: ToolRequest) -> Any:
        try:
            return tools.invoke(request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Tool not found") from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        except (ValueError, TypeError, RuntimeError) as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/orders")
    def list_orders(
        account_id: str | None = Query(default=None),
        state: OrderState | None = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in orders.list(account_id=account_id, state=state)[
                offset : offset + limit
            ]
        ]

    @router.post("/orders")
    def create_order_intent(intent: OrderIntent) -> dict[str, Any]:
        try:
            return orders.create(intent).model_dump(mode="json")
        except OrderConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except (ValueError, AuditOutboxPending) as exc:
            raise HTTPException(
                status_code=422 if isinstance(exc, ValueError) else 503,
                detail=public_error_detail(exc),
            ) from exc

    @router.get("/orders/{order_id}")
    def get_order(order_id: str) -> dict[str, Any]:
        try:
            return orders.get(order_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Order not found") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/orders/{order_id}/events")
    def append_order_event(
        order_id: str, request: OrderAppendRequest
    ) -> dict[str, Any]:
        if request.event.order_id != order_id:
            raise HTTPException(
                status_code=422, detail="Order event path identifier mismatch"
            )
        try:
            return orders.append(
                request.event, expected_revision=request.expected_revision
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Order not found") from exc
        except OrderConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except (ValueError, AuditOutboxPending) as exc:
            raise HTTPException(
                status_code=422 if isinstance(exc, ValueError) else 503,
                detail=public_error_detail(exc),
            ) from exc

    @router.get("/orders/{order_id}/verify")
    def verify_order(order_id: str) -> dict[str, Any]:
        try:
            return orders.verify(order_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Order not found") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/orders/{order_id}/reconcile")
    def reconcile_order(
        order_id: str, request: OrderReconciliationRequestEnvelope
    ) -> dict[str, Any]:
        try:
            return orders.reconcile(order_id, request.observation).model_dump(
                mode="json"
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Order not found") from exc

    @router.post("/orders/{order_id}/accounting-sync")
    def sync_order_accounting(
        order_id: str,
        actor: str = Header(default="accounting-reconciler", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return order_accounting_sync.sync(order_id, actor=actor).model_dump(
                mode="json"
            )
        except KeyError as exc:
            detail = "Order or accounting account not found"
            raise HTTPException(status_code=404, detail=detail) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/orders/{order_id}/accounting-events")
    def order_accounting_events(order_id: str) -> list[dict[str, Any]]:
        try:
            return [
                item.model_dump(mode="json")
                for item in orders.accounting_events(order_id)
            ]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Order not found") from exc

    @router.post("/performance/lots")
    def analyse_lot_performance(request: LotAnalysisRequest) -> dict[str, Any]:
        try:
            return analyse_lots(
                request.fills,
                base_currency=request.base_currency,
                method=request.method,
                marks=request.marks,
            ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/execution/calibrate")
    def calibrate_execution_endpoint(
        request: ExecutionCalibrationRequest,
    ) -> dict[str, Any]:
        try:
            return calibrate_execution(
                request.observations,
                base_policy=request.base_policy,
                thresholds=request.thresholds,
            ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/lineage")
    def lineage_snapshot() -> dict[str, Any]:
        return lineage.snapshot().model_dump(mode="json")

    @router.get("/lineage/nodes")
    def list_lineage_nodes(
        kind: LineageNodeKind | None = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in lineage.list_nodes(kind=kind)[offset : offset + limit]
        ]

    @router.post("/lineage/nodes")
    def add_lineage_node(
        request: LineageNodeSaveRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return lineage.add_node(
                request.node, expected_revision=request.expected_revision, actor=actor
            ).model_dump(mode="json")
        except LineageConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/lineage/edges")
    def list_lineage_edges() -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in lineage.list_edges()]

    @router.post("/lineage/edges")
    def add_lineage_edge(request: LineageEdgeSaveRequest) -> dict[str, Any]:
        try:
            return lineage.add_edge(
                request.edge, expected_revision=request.expected_revision
            ).model_dump(mode="json")
        except LineageConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/lineage/nodes/{node_id}/impact")
    def lineage_impact(
        node_id: str, max_nodes: int = Query(default=10_000, ge=1, le=10_000)
    ) -> dict[str, Any]:
        try:
            return lineage.impact(node_id, max_nodes=max_nodes).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Lineage node not found"
            ) from exc

    @router.get("/lineage/nodes/{node_id}/ancestry")
    def lineage_ancestry(
        node_id: str, max_nodes: int = Query(default=10_000, ge=1, le=10_000)
    ) -> dict[str, Any]:
        try:
            return lineage.ancestry(node_id, max_nodes=max_nodes).model_dump(
                mode="json"
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Lineage node not found"
            ) from exc

    @router.get("/lineage/nodes/{node_id}/bundle")
    def lineage_bundle(
        node_id: str, descendants: bool = Query(default=False)
    ) -> dict[str, Any]:
        try:
            return lineage.export_bundle(
                node_id, include_descendants=descendants
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Lineage node not found"
            ) from exc

    @router.get("/budgets")
    def list_budgets(
        scope_id: str | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in budgets.list_budgets(scope_id=scope_id)
        ]

    @router.post("/budgets")
    def create_budget(request: BudgetCreate) -> dict[str, Any]:
        try:
            return budgets.create(request).model_dump(mode="json")
        except BudgetConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/budgets/reservations")
    def reserve_budget(request: BudgetReservationCreate) -> dict[str, Any]:
        try:
            return budgets.reserve(request).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Budget not found") from exc
        except BudgetConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc

    @router.get("/budgets/reservations/{reservation_id}")
    def get_budget_reservation(reservation_id: str) -> dict[str, Any]:
        try:
            return budgets.get_reservation(reservation_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Reservation not found"
            ) from exc

    @router.post("/budgets/reservations/{reservation_id}/commit")
    def commit_budget_reservation(
        reservation_id: str,
        request: BudgetCommitRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return budgets.commit(
                reservation_id,
                actual_amount=request.actual_amount,
                expected_revision=request.expected_revision,
                actor=actor,
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Reservation not found"
            ) from exc
        except BudgetConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc

    @router.post("/budgets/reservations/{reservation_id}/release")
    def release_budget_reservation(
        reservation_id: str,
        request: BudgetReleaseRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return budgets.release(
                reservation_id,
                expected_revision=request.expected_revision,
                actor=actor,
                reason=request.reason,
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Reservation not found"
            ) from exc
        except BudgetConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc

    @router.post("/budgets/reconcile-expired")
    def reconcile_expired_budgets(
        actor: str = Header(default="budget-reconciler", alias="X-Actor"),
    ) -> dict[str, Any]:
        return budgets.reconcile_expired(actor=actor)

    @router.get("/budgets/{budget_id}")
    def get_budget(budget_id: str) -> dict[str, Any]:
        try:
            return budgets.get_budget(budget_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Budget not found") from exc

    @router.post("/accounting/corporate-actions/plan")
    def plan_corporate_action_endpoint(
        request: CorporateActionRequest,
    ) -> dict[str, Any]:
        try:
            return plan_corporate_action(request).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/accounting/settlement/evaluate")
    def evaluate_settlement_endpoint(
        request: SettlementEvaluationRequest,
    ) -> dict[str, Any]:
        try:
            return evaluate_settlement(
                as_of=request.as_of,
                settled_cash=request.settled_cash,
                obligations=request.obligations,
            ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/accounting/accounts")
    def list_accounting_accounts() -> list[str]:
        return accounting.list_account_ids()

    @router.post("/accounting/accounts/{account_id}")
    def create_accounting_account(
        account_id: str,
        request: AccountCreateRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return accounting.create(
                account_id, request.initial_cash, actor=actor
            ).model_dump(mode="json")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc

    @router.get("/accounting/accounts/{account_id}")
    def get_accounting_account(account_id: str) -> dict[str, Any]:
        if not accounting.exists(account_id):
            raise HTTPException(status_code=404, detail="Accounting account not found")
        try:
            return accounting.account(account_id).snapshot().model_dump(mode="json")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/accounting/accounts/{account_id}/valuation")
    def value_account_endpoint(
        account_id: str, request: AccountValuationRequest
    ) -> dict[str, Any]:
        if not accounting.exists(account_id):
            raise HTTPException(status_code=404, detail="Accounting account not found")
        try:
            return value_account(
                accounting.account(account_id).snapshot(),
                base_currency=request.base_currency,
                quotes=request.quotes,
                valued_at=request.valued_at,
                max_fx_age_seconds=request.max_fx_age_seconds,
            ).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/accounting/accounts/{account_id}/events")
    def apply_accounting_event(
        account_id: str,
        event: AccountingEvent,
        actor: str = Header(default="accounting-engine", alias="X-Actor"),
    ) -> dict[str, Any]:
        if not accounting.exists(account_id):
            raise HTTPException(status_code=404, detail="Accounting account not found")
        if event.account_id != account_id:
            raise HTTPException(
                status_code=422, detail="event account_id does not match route"
            )
        try:
            return (
                accounting.account(account_id)
                .apply(event, actor=actor)
                .model_dump(mode="json")
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/accounting/accounts/{account_id}/reconcile")
    def reconcile_accounting_account(
        account_id: str, request: AccountReconciliationRequest
    ) -> dict[str, Any]:
        if not accounting.exists(account_id):
            raise HTTPException(status_code=404, detail="Accounting account not found")
        try:
            return (
                accounting.account(account_id)
                .reconcile(request.observed, request.tolerance)
                .model_dump(mode="json")
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/accounting/accounts/{account_id}/verify")
    def verify_accounting_account(account_id: str) -> dict[str, Any]:
        if not accounting.exists(account_id):
            raise HTTPException(status_code=404, detail="Accounting account not found")
        try:
            return accounting.account(account_id).verify()
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/reproducibility/manifests")
    def create_run_manifest(
        request: RunManifestCreate,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return manifests.create(request, actor=actor).model_dump(mode="json")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/reproducibility/manifests/{manifest_id}")
    def get_run_manifest(manifest_id: str) -> dict[str, Any]:
        try:
            manifest = manifests.get(manifest_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Run manifest not found"
            ) from exc
        return {
            **manifest.model_dump(mode="json"),
            "verification": verify_manifest(manifest),
        }

    @router.get("/reproducibility/experiments/{experiment_id}/manifests")
    def list_run_manifests(experiment_id: str) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in manifests.list_for_experiment(experiment_id)
        ]

    @router.get("/monitoring/drift/baselines")
    def list_drift_baselines() -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in drift_baselines.list()]

    @router.post("/monitoring/drift/baselines")
    def create_drift_baseline(
        request: DriftBaselineSaveRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return drift_baselines.create(
                request.baseline,
                actor=actor,
                expected_store_revision=request.expected_store_revision,
            ).model_dump(mode="json")
        except RuntimeError as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/monitoring/drift/baselines/{baseline_id}/evaluate")
    def evaluate_drift_endpoint(
        baseline_id: str, request: DriftEvaluationRequest
    ) -> dict[str, Any]:
        try:
            baseline = drift_baselines.get(baseline_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Drift baseline not found"
            ) from exc
        return evaluate_drift(baseline, request).model_dump(mode="json")

    @router.get("/operations/incidents")
    def list_incidents() -> list[dict[str, Any]]:
        try:
            return [item.model_dump(mode="json") for item in incidents.list()]
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/operations/incidents")
    def create_incident(request: IncidentCreate) -> dict[str, Any]:
        try:
            return incidents.create(request).model_dump(mode="json")
        except IncidentConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/operations/incidents/{incident_id}")
    def get_incident(incident_id: str) -> dict[str, Any]:
        try:
            return incidents.get(incident_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Incident not found") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/operations/incidents/{incident_id}/events")
    def append_incident_event(
        incident_id: str, request: IncidentAppendRequest
    ) -> dict[str, Any]:
        if request.event.incident_id != incident_id:
            raise HTTPException(
                status_code=422, detail="event incident_id does not match route"
            )
        try:
            return incidents.append(
                request.event, expected_revision=request.expected_revision
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Incident not found") from exc
        except IncidentConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/operations/incidents/readiness")
    def incident_readiness(request: IncidentReadinessRequest) -> dict[str, Any]:
        try:
            if request.incident_ids is None:
                selected = incidents.list()
            else:
                selected = [incidents.get(item) for item in request.incident_ids]
            return evaluate_incident_readiness(
                selected, evaluated_at=request.evaluated_at
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Incident not found") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/operations/snapshots")
    def list_snapshots() -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in snapshots.list()]

    @router.post("/operations/snapshots")
    def create_snapshot(
        request: SnapshotCreateRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return snapshots.create(
                request.relative_paths, snapshot_id=request.snapshot_id, actor=actor
            ).model_dump(mode="json")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/operations/snapshots/{snapshot_id}/verify")
    def verify_snapshot(snapshot_id: str) -> dict[str, Any]:
        try:
            return snapshots.verify(snapshot_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(
                status_code=404, detail=public_error_detail(exc)
            ) from exc

    @router.get("/operations/snapshots/{snapshot_id}/restore-plan")
    def plan_snapshot_restore(snapshot_id: str) -> dict[str, Any]:
        try:
            return snapshots.plan_restore(snapshot_id).model_dump(mode="json")
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/operations/snapshots/{snapshot_id}/restore-drill")
    def drill_snapshot_restore(snapshot_id: str) -> dict[str, Any]:
        try:
            return snapshots.drill_restore(snapshot_id).model_dump(mode="json")
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Snapshot not found") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.post("/operations/attestations/verify")
    def verify_build_attestation(request: AttestationVerifyRequest) -> dict[str, Any]:
        try:
            return verify_attestation(request.attestation)
        except ValueError as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/operations/migrations/plan")
    def plan_migration_endpoint(request: MigrationPlanRequest) -> dict[str, Any]:
        try:
            return plan_migration(request).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/operations/supply-chain")
    def supply_chain_status() -> dict[str, Any]:
        root = Path(__file__).resolve().parents[3]
        try:
            return verify_dependency_policy(root)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.get("/governance/triggers")
    def list_triggers(
        state: TriggerState | None = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        values = triggers.list(state=state)
        return [
            item.model_dump(mode="json") for item in values[offset : offset + limit]
        ]

    @router.post("/governance/triggers")
    def create_trigger(
        request: TriggerCreate,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return triggers.create(request, actor=actor).model_dump(mode="json")
        except TriggerConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except GovernanceAuditPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/governance/triggers/{trigger_id}/state")
    def set_trigger_state(
        trigger_id: str,
        request: TriggerStateRequest,
        actor: str = Header(default="api-operator", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            return triggers.update_state(
                trigger_id,
                actor=actor,
                **request.model_dump(),
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Trigger not found") from exc
        except TriggerConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except GovernanceAuditPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/governance/triggers/{trigger_id}/evaluate")
    def evaluate_trigger_endpoint(
        trigger_id: str,
        request: TriggerEvaluationRequest,
        actor: str = Header(default="trigger-engine", alias="X-Actor"),
    ) -> dict[str, Any]:
        try:
            proposal = trigger_service.evaluate_and_propose(
                trigger_id,
                actor=actor,
                now=request.evaluated_at,
                **request.model_dump(exclude={"evaluated_at"}),
            )
            return {
                "matched": proposal is not None,
                "proposal": proposal.model_dump(mode="json") if proposal else None,
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Trigger not found") from exc
        except (TriggerConflict, GovernanceConflict) as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except GovernanceAuditPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=public_error_detail(exc)
            ) from exc

    @router.get("/governance/proposals")
    def list_proposals(
        state: ProposalState | None = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        values = governance.list(state=state)
        return [
            item.model_dump(mode="json") for item in values[offset : offset + limit]
        ]

    @router.post("/governance/proposals")
    def create_proposal(
        request: ProposalCreate,
        actor: str = Header(default="api-operator", alias="X-Actor"),
        actor_key: str | None = Header(default=None, alias="X-Actor-Key"),
    ) -> dict[str, Any]:
        if governance.requires_separate_approval_key(request.risk_tier):
            actor_authorised(actor, actor_key, required_role="proposer")
        try:
            return governance.create(request, actor=actor).model_dump(mode="json")
        except GovernanceConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except GovernanceAuditPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/governance/proposals/{proposal_id}/decision")
    def decide_proposal(
        proposal_id: str,
        request: DecisionRequest,
        approval_key: str | None = Header(default=None, alias="X-Approval-Key"),
        actor_key: str | None = Header(default=None, alias="X-Actor-Key"),
    ) -> dict[str, Any]:
        proposal_actor_authorised(
            proposal_id,
            request.actor,
            actor_key,
            required_role="approver",
        )
        if request.decision == "approve":
            approval_authorised(
                proposal_id,
                approval_key,
                actor=request.actor,
                actor_key=actor_key,
                required_role="approver",
            )
        try:
            return governance.decide(proposal_id, request).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Proposal not found") from exc
        except GovernanceConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except GovernanceAuditPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/governance/proposals/{proposal_id}/consume")
    def consume_proposal(
        proposal_id: str,
        request: ConsumeRequest,
        approval_key: str | None = Header(default=None, alias="X-Approval-Key"),
        actor_key: str | None = Header(default=None, alias="X-Actor-Key"),
    ) -> dict[str, Any]:
        approval_authorised(
            proposal_id,
            approval_key,
            actor=request.actor,
            actor_key=actor_key,
            required_role="executor",
        )
        try:
            return governance.consume(proposal_id, request).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Proposal not found") from exc
        except GovernanceConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except GovernanceAuditPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.get("/coordination/leases")
    def list_coordination_leases(now: datetime | None = None) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in leases.list(now=now)]

    @router.post("/coordination/leases")
    def acquire_coordination_lease(request: LeaseAcquire) -> dict[str, Any]:
        try:
            return leases.acquire(request).model_dump(mode="json")
        except LeaseConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc
        except AuditOutboxPending as exc:
            raise HTTPException(
                status_code=503, detail=public_error_detail(exc)
            ) from exc

    @router.post("/coordination/leases/{lease_id}/renew")
    def renew_coordination_lease(
        lease_id: str, request: LeaseRenewRequest
    ) -> dict[str, Any]:
        try:
            return leases.renew(
                lease_id,
                owner_id=request.owner_id,
                fencing_token=request.fencing_token,
                now=request.now,
                ttl_seconds=request.ttl_seconds,
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Lease not found") from exc
        except LeaseConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc

    @router.post("/coordination/leases/{lease_id}/release")
    def release_coordination_lease(
        lease_id: str, request: LeaseReleaseRequest
    ) -> dict[str, Any]:
        try:
            return leases.release(
                lease_id,
                owner_id=request.owner_id,
                fencing_token=request.fencing_token,
                now=request.now,
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Lease not found") from exc
        except LeaseConflict as exc:
            raise HTTPException(
                status_code=409, detail=public_error_detail(exc)
            ) from exc

    @router.get("/governance/audit/verify")
    def verify_audit() -> dict[str, Any]:
        health = reconcile_all_audit(fail_closed=False)
        return {
            **health["ledger"],
            "audit_pending": health["pending"],
            "audit_delivered": health["delivered"],
            "outboxes": health["stores"],
            **(
                {"degraded_stores": health["degraded_stores"]}
                if "degraded_stores" in health
                else {}
            ),
        }

    @router.post("/governance/audit/reconcile")
    def reconcile_audit() -> dict[str, Any]:
        return reconcile_all_audit(fail_closed=True)

    @router.get("/governance/audit")
    def audit_tail(
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        return governance.ledger.tail(limit)

    router.include_router(create_evaluation_router())
    return router
