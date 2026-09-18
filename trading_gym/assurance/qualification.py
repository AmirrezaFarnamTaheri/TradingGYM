"""Cross-plane qualification reports for human promotion review.

This module aggregates independently generated evidence. It deliberately does
not activate models, approve governance proposals, or grant execution authority.
A positive report means only that the supplied evidence meets the configured
review gate and is internally coherent.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from trading_gym.accounting import ReconciliationReport
from trading_gym.execution.simulation import ScenarioResult, SimulatedOrderStatus
from trading_gym.experiments import RunManifest, verify_manifest
from trading_gym.monitoring.drift import DriftEvaluation
from trading_gym.operations import SLOEvaluation


class QualificationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    require_valid_manifest: bool = True
    require_valid_ledger: bool = True
    require_sealed_ledger: bool = True
    require_zero_pending_audit: bool = True
    require_reconciled_accounts: bool = True
    allow_warning_drift: bool = True
    allow_at_risk_slo: bool = False
    minimum_extreme_fill_fraction: Annotated[float, Field(ge=0.0, le=1.0)] = 0.10
    maximum_extreme_cost_bps: Annotated[float, Field(ge=0.0, le=100_000.0)] = 500.0
    minimum_drift_reports: Annotated[int, Field(ge=0, le=10_000)] = 1
    minimum_execution_scenarios: Annotated[int, Field(ge=0, le=10_000)] = 1
    minimum_reconciliations: Annotated[int, Field(ge=0, le=10_000)] = 1
    minimum_slo_evaluations: Annotated[int, Field(ge=0, le=10_000)] = 1


class AuditEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    sealed: bool
    pending: Annotated[int, Field(ge=0, le=10_000_000)] = 0


class QualificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: Annotated[str, Field(min_length=1, max_length=200)]
    manifest: RunManifest
    drift: list[DriftEvaluation] = Field(default_factory=list, max_length=10_000)
    execution_scenarios: list[ScenarioResult] = Field(
        default_factory=list, max_length=10_000
    )
    reconciliations: list[ReconciliationReport] = Field(
        default_factory=list, max_length=10_000
    )
    slos: list[SLOEvaluation] = Field(default_factory=list, max_length=10_000)
    audit: AuditEvidence
    policy: QualificationPolicy = Field(default_factory=QualificationPolicy)


class QualificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    ready_for_human_review: bool
    blockers: list[str]
    warnings: list[str]
    evidence_counts: dict[str, int]
    evidence_sha256: str
    promotion_authority_granted: bool = False
    execution_authority_granted: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def qualify_candidate(request: QualificationRequest) -> QualificationReport:
    blockers: list[str] = []
    warnings: list[str] = []
    policy = request.policy

    manifest_status = verify_manifest(request.manifest)
    if policy.require_valid_manifest and not manifest_status["valid"]:
        blockers.append("manifest_invalid")
    if policy.require_valid_ledger and not request.audit.valid:
        blockers.append("audit_ledger_invalid")
    if policy.require_sealed_ledger and not request.audit.sealed:
        blockers.append("audit_ledger_unsealed")
    if policy.require_zero_pending_audit and request.audit.pending:
        blockers.append("audit_outbox_pending")

    required_counts = {
        "drift": (len(request.drift), policy.minimum_drift_reports),
        "execution_scenarios": (
            len(request.execution_scenarios),
            policy.minimum_execution_scenarios,
        ),
        "reconciliations": (
            len(request.reconciliations),
            policy.minimum_reconciliations,
        ),
        "slos": (len(request.slos), policy.minimum_slo_evaluations),
    }
    for name, (observed, minimum) in required_counts.items():
        if observed < minimum:
            blockers.append(f"evidence_missing:{name}:{observed}/{minimum}")

    for item in request.drift:
        if item.severity == "critical":
            blockers.append(f"critical_drift:{item.feature_name}")
        elif item.severity == "warning":
            destination = warnings if policy.allow_warning_drift else blockers
            destination.append(f"warning_drift:{item.feature_name}")
        elif item.severity == "insufficient_data":
            blockers.append(f"drift_evidence_insufficient:{item.feature_name}")

    for index, scenarios in enumerate(request.execution_scenarios):
        extreme = scenarios.extreme
        fill_fraction = extreme.filled_quantity / extreme.requested_quantity
        cost_bps = (
            extreme.total_cost / extreme.notional * 10_000.0
            if extreme.notional > 0
            else float("inf")
        )
        if extreme.status is SimulatedOrderStatus.REJECTED:
            blockers.append(f"extreme_execution_rejected:{index}")
        if fill_fraction < policy.minimum_extreme_fill_fraction:
            blockers.append(f"extreme_fill_fraction:{index}")
        if cost_bps > policy.maximum_extreme_cost_bps:
            blockers.append(f"extreme_execution_cost:{index}")
        if scenarios.adverse.status is not SimulatedOrderStatus.FILLED:
            warnings.append(f"adverse_execution_not_fully_filled:{index}")

    for reconciliation in request.reconciliations:
        if not reconciliation.matched:
            destination = blockers if policy.require_reconciled_accounts else warnings
            destination.append(f"account_unreconciled:{reconciliation.account_id}")
        if reconciliation.mutation_performed:
            blockers.append(f"reconciliation_mutated_state:{reconciliation.account_id}")

    for slo in request.slos:
        if slo.status in {"breached", "insufficient_data"}:
            blockers.append(f"slo_{slo.status}:{slo.name}")
        elif slo.status == "at_risk":
            destination = warnings if policy.allow_at_risk_slo else blockers
            destination.append(f"slo_at_risk:{slo.name}")

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    evidence_payload = request.model_dump(mode="json")
    evidence_sha256 = hashlib.sha256(_canonical(evidence_payload)).hexdigest()
    return QualificationReport(
        candidate_id=request.candidate_id,
        ready_for_human_review=not blockers,
        blockers=blockers,
        warnings=warnings,
        evidence_counts={
            "drift": len(request.drift),
            "execution_scenarios": len(request.execution_scenarios),
            "reconciliations": len(request.reconciliations),
            "slos": len(request.slos),
        },
        evidence_sha256=evidence_sha256,
    )
