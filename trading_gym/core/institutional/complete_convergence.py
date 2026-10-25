"""Sixth-pass, twenty-domain convergence qualification.

The gate is deliberately non-authoritative.  It validates that every requested
institutional capability has current, internally consistent evidence and that
none of the reports claims mutation or operational authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.core.assurance import ExpandedQualificationReport
from .agent_memory import MemoryQueryReport
from .agent_workforce import WorkforcePlanReport
from .alpha_factory import AlphaFactoryReport
from .causal_research import CausalResearchReport
from .data_platform import DataPlatformReport
from .distributed_chaos import DistributedChaosReport
from .enterprise_risk import EnterpriseRiskReport
from .execution_quality import ExecutionQualityReport
from .explainability import ExplainabilityReport
from .market_intelligence import MarketRegimeReport
from .microstructure import MicrostructureReport
from .model_lifecycle import LifecycleTransitionReport
from .policy_compliance import PolicyComplianceReport
from .portfolio_construction import PortfolioConstructionReport
from .portfolio_twin import PortfolioTwinReport
from .product_collaboration import ProductCollaborationReport
from .routing import RoutingReport
from .scientific_lab import ScientificLabReport
from .signal_graph import SignalGraphReport
from .source_mining import SourceMiningReport
from .base import require_aware, sha256_payload

DOMAIN_FIELDS = (
    "source_mining",
    "market_intelligence",
    "alpha_factory",
    "signal_graph",
    "portfolio_construction",
    "portfolio_twin",
    "microstructure",
    "routing",
    "execution_quality",
    "agent_workforce",
    "agent_memory",
    "model_lifecycle",
    "explainability",
    "causal_research",
    "data_platform",
    "enterprise_risk",
    "policy_compliance",
    "distributed_chaos",
    "product_collaboration",
    "scientific_lab",
)

AUTHORITY_TRUE_FIELDS = {
    "repository_authority_granted",
    "trading_authority_granted",
    "promotion_authority_granted",
    "experiment_authority_granted",
    "graph_mutation_performed",
    "execution_authority_granted",
    "order_submission_authority_granted",
    "routing_authority_granted",
    "enforcement_authority_granted",
    "task_execution_performed",
    "external_authority_granted",
    "memory_mutation_performed",
    "decision_authority_granted",
    "state_transition_performed",
    "model_mutation_performed",
    "causal_conclusion_authority_granted",
    "intervention_performed",
    "storage_mutation_performed",
    "feature_serving_authority_granted",
    "limit_mutation_performed",
    "policy_activation_authority_granted",
    "compliance_conclusion_reached",
    "scheduling_mutation_performed",
    "chaos_injection_performed",
    "leader_election_authority_granted",
    "package_installation_authority_granted",
    "review_state_mutation_performed",
    "ui_authority_granted",
    "leaderboard_publication_authority_granted",
    "strategy_registration_authority_granted",
    "mutation_performed",
}


class CompleteConvergencePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_evidence_age_seconds: Annotated[int, Field(ge=60, le=10 * 365 * 86400)] = (
        86400
    )
    maximum_future_skew_seconds: Annotated[int, Field(ge=0, le=86400)] = 60
    require_unique_report_hashes: bool = True
    require_base_qualification_ready: bool = True


class CompleteConvergenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    policy: CompleteConvergencePolicy = Field(default_factory=CompleteConvergencePolicy)
    base_qualification: ExpandedQualificationReport
    source_mining: SourceMiningReport
    market_intelligence: MarketRegimeReport
    alpha_factory: AlphaFactoryReport
    signal_graph: SignalGraphReport
    portfolio_construction: PortfolioConstructionReport
    portfolio_twin: PortfolioTwinReport
    microstructure: MicrostructureReport
    routing: RoutingReport
    execution_quality: ExecutionQualityReport
    agent_workforce: WorkforcePlanReport
    agent_memory: MemoryQueryReport
    model_lifecycle: LifecycleTransitionReport
    explainability: ExplainabilityReport
    causal_research: CausalResearchReport
    data_platform: DataPlatformReport
    enterprise_risk: EnterpriseRiskReport
    policy_compliance: PolicyComplianceReport
    distributed_chaos: DistributedChaosReport
    product_collaboration: ProductCollaborationReport
    scientific_lab: ScientificLabReport
    evidence_observed_at: dict[str, datetime]

    @model_validator(mode="after")
    def validate_times(self) -> "CompleteConvergenceRequest":
        require_aware(self.evaluated_at, "evaluated_at")
        for key, value in self.evidence_observed_at.items():
            require_aware(value, f"evidence_observed_at:{key}")
        return self


class CompleteConvergenceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    domain_hashes: dict[str, str]
    evidence_age_seconds: dict[str, int]
    blockers: list[str]
    warnings: list[str]
    ready_for_human_review: bool
    completion_ratio: float
    report_sha256: str
    mutation_performed: bool = False
    promotion_authority_granted: bool = False
    trading_authority_granted: bool = False
    deployment_authority_granted: bool = False


def _report_hash(report: BaseModel) -> str | None:
    value = getattr(report, "report_sha256", None)
    return value if isinstance(value, str) else None


def _computed_report_hash(report: BaseModel) -> str:
    defaulted_top_level_fields = {
        name
        for name, field in type(report).model_fields.items()
        if not field.is_required()
    }
    data = report.model_dump(
        mode="json",
        exclude={"report_sha256", *defaulted_top_level_fields},
    )
    return sha256_payload(data)


def _inspect_authority(report: BaseModel, domain: str) -> list[str]:
    data = report.model_dump(mode="python")
    violations: list[str] = []
    for key in AUTHORITY_TRUE_FIELDS:
        if data.get(key) is True:
            violations.append(f"authority_violation:{domain}:{key}")
    return violations


def qualify_complete_convergence(
    request: CompleteConvergenceRequest,
) -> CompleteConvergenceReport:
    blockers: list[str] = []
    warnings: list[str] = []
    ages: dict[str, int] = {}
    hashes: dict[str, str] = {}
    invalid_domains: set[str] = set()
    reports: dict[str, BaseModel] = {
        name: getattr(request, name) for name in DOMAIN_FIELDS
    }
    required_timestamps = {"base", *DOMAIN_FIELDS}
    for name in sorted(required_timestamps):
        observed = request.evidence_observed_at.get(name)
        if observed is None:
            blockers.append(f"evidence_timestamp_missing:{name}")
            if name in DOMAIN_FIELDS:
                invalid_domains.add(name)
            continue
        delta = request.evaluated_at - observed
        if delta < -timedelta(seconds=request.policy.maximum_future_skew_seconds):
            blockers.append(f"evidence_timestamp_future:{name}")
            if name in DOMAIN_FIELDS:
                invalid_domains.add(name)
            continue
        age = max(0, int(delta.total_seconds()))
        ages[name] = age
        if age > request.policy.maximum_evidence_age_seconds:
            blockers.append(f"evidence_stale:{name}:{age}")
            if name in DOMAIN_FIELDS:
                invalid_domains.add(name)
    for name in sorted(set(request.evidence_observed_at) - required_timestamps):
        warnings.append(f"unrecognised_evidence_timestamp:{name}")
    base_data = request.base_qualification.model_dump(mode="python")
    base_ready = bool(base_data.get("ready_for_human_review"))
    for key in AUTHORITY_TRUE_FIELDS | {
        "failover_authority_granted",
        "restore_authority_granted",
        "budget_consumption_authority_granted",
    }:
        if base_data.get(key) is True:
            blockers.append(f"base_authority_violation:{key}")
    base_blockers = list(base_data.get("blockers") or [])
    if request.policy.require_base_qualification_ready and (
        not base_ready or base_blockers
    ):
        blockers.append("base_qualification_not_ready")
    base_hash = _report_hash(request.base_qualification)
    if base_hash:
        hashes["base"] = base_hash
    else:
        hashes["base"] = sha256_payload(base_data)
    for name, report in reports.items():
        domain_invalid = name in invalid_domains
        data = report.model_dump(mode="python")
        report_blockers = list(data.get("blockers") or [])
        if report_blockers:
            blockers.append(f"domain_blocked:{name}")
            domain_invalid = True
        readiness_fields = [
            key
            for key in data
            if key.startswith("ready_for_")
            or key.endswith("_reached")
            or key == "converged"
            or key == "saturation_reached"
        ]
        for key in readiness_fields:
            if key == "compliance_conclusion_reached":
                continue
            if data.get(key) is False and key in {
                "ready_for_human_review",
                "ready_for_peer_review",
                "ready_for_governance_review",
                "ready_for_scientific_review",
                "saturation_reached",
                "converged",
            }:
                blockers.append(f"domain_not_ready:{name}:{key}")
                domain_invalid = True
        authority_violations = _inspect_authority(report, name)
        blockers.extend(authority_violations)
        if authority_violations:
            domain_invalid = True
        digest = _report_hash(report)
        computed_digest = _computed_report_hash(report)
        if digest is None:
            blockers.append(f"report_hash_missing:{name}")
            domain_invalid = True
            digest = computed_digest
        elif digest != computed_digest:
            blockers.append(f"report_hash_mismatch:{name}")
            domain_invalid = True
        hashes[name] = digest
        if domain_invalid:
            invalid_domains.add(name)
    if request.policy.require_unique_report_hashes and len(set(hashes.values())) != len(
        hashes
    ):
        blockers.append("duplicate_report_hashes")
        by_digest: dict[str, list[str]] = {}
        for name, digest in hashes.items():
            by_digest.setdefault(digest, []).append(name)
        for names in by_digest.values():
            if len(names) > 1:
                invalid_domains.update(name for name in names if name in DOMAIN_FIELDS)
    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    completion = (len(DOMAIN_FIELDS) - len(invalid_domains)) / len(DOMAIN_FIELDS)
    payload = {
        "evaluated_at": request.evaluated_at,
        "domain_hashes": dict(sorted(hashes.items())),
        "evidence_age_seconds": dict(sorted(ages.items())),
        "blockers": blockers,
        "warnings": warnings,
        "ready_for_human_review": not blockers,
        "completion_ratio": completion,
    }
    return CompleteConvergenceReport(**payload, report_sha256=sha256_payload(payload))
