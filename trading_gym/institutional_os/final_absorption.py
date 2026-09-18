"""Final donor-absorption and saturation assurance planes.

These evaluators close the remaining semantic gaps after the institutional OS:
behavioural equivalence, feature-store parity, deterministic market replay,
financing/inventory constraints, exact P&L attribution, governed knowledge and
workflow review, dynamic/mutation coverage, and a cumulative donor-saturation
verdict.  They are deliberately non-authoritative and perform no trading,
fund movement, deployment, package installation, or external tool execution.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import (
    ensure_unique,
    require_aware,
    require_decimal,
    require_identifier,
    sha256_payload,
)

HEX = r"^[0-9a-f]{64}$"
MAX_RECORDS = 100_000


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNVERIFIED = "unverified"


class BehaviouralBenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str
    donor: str
    responsibility: str
    donor_evidence_sha256: Annotated[str, Field(pattern=HEX)]
    normalized_input_sha256: Annotated[str, Field(pattern=HEX)]
    donor_output_sha256: Annotated[str, Field(pattern=HEX)]
    target_output_sha256: Annotated[str, Field(pattern=HEX)]
    oracle: str
    allowed_divergence: str = "none"
    verdict: Verdict
    evidence_level: str = "static-contract"
    donor_defect_intentionally_not_preserved: bool = False

    @model_validator(mode="after")
    def valid(self) -> "BehaviouralBenchmarkCase":
        require_identifier(self.case_id, "case_id")
        require_identifier(self.donor, "donor")
        if len(self.responsibility.strip()) < 3 or len(self.oracle.strip()) < 3:
            raise ValueError("responsibility and oracle are required")
        if self.evidence_level not in {"static-contract", "differential-runtime"}:
            raise ValueError("invalid evidence_level")
        return self


class BehaviouralBenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_donors: list[str] = Field(min_length=1, max_length=128)
    cases: list[BehaviouralBenchmarkCase] = Field(min_length=1, max_length=MAX_RECORDS)
    minimum_cases_per_donor: int = Field(default=1, ge=1, le=10_000)

    @model_validator(mode="after")
    def unique(self) -> "BehaviouralBenchmarkRequest":
        ensure_unique(self.expected_donors, "expected_donors")
        ensure_unique((case.case_id for case in self.cases), "benchmark case IDs")
        return self


class BehaviouralBenchmarkReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    donor_case_counts: dict[str, int]
    passed: int
    failed: int
    unverified: int
    static_contract_cases: int
    differential_runtime_cases: int
    blockers: list[str]
    ready_for_human_review: bool
    report_sha256: Annotated[str, Field(pattern=HEX)]
    execution_authority_granted: bool = False


def evaluate_behavioural_benchmarks(
    request: BehaviouralBenchmarkRequest,
) -> BehaviouralBenchmarkReport:
    counts: dict[str, int] = defaultdict(int)
    blockers: list[str] = []
    passed = failed = unverified = 0
    static_contract = differential_runtime = 0
    expected = set(request.expected_donors)
    for case in request.cases:
        if case.donor not in expected:
            blockers.append(f"unexpected_donor:{case.donor}")
        counts[case.donor] += 1
        if case.evidence_level == "static-contract":
            static_contract += 1
        else:
            differential_runtime += 1
        if case.verdict is Verdict.PASS:
            passed += 1
        elif case.verdict is Verdict.FAIL:
            failed += 1
            blockers.append(f"benchmark_failed:{case.case_id}")
        else:
            unverified += 1
            blockers.append(f"benchmark_unverified:{case.case_id}")
    for donor in sorted(expected):
        if counts.get(donor, 0) < request.minimum_cases_per_donor:
            blockers.append(f"insufficient_donor_benchmarks:{donor}")
    payload = {
        "donor_case_counts": dict(sorted(counts.items())),
        "passed": passed,
        "failed": failed,
        "unverified": unverified,
        "static_contract_cases": static_contract,
        "differential_runtime_cases": differential_runtime,
        "blockers": sorted(set(blockers)),
        "ready_for_human_review": not blockers,
    }
    return BehaviouralBenchmarkReport(**payload, report_sha256=sha256_payload(payload))


class FeatureContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    feature_id: str
    owner: str
    unit: str
    frequency: str
    definition_sha256: Annotated[str, Field(pattern=HEX)]
    source_node_ids: list[str] = Field(min_length=1, max_length=10_000)
    maximum_age_seconds: int = Field(ge=1, le=10 * 365 * 86400)
    missingness_policy: str
    deprecation_state: str = "active"

    @model_validator(mode="after")
    def valid(self) -> "FeatureContract":
        require_identifier(self.feature_id, "feature_id")
        ensure_unique(self.source_node_ids, "source_node_ids")
        if self.deprecation_state not in {"active", "deprecated", "retired"}:
            raise ValueError("invalid deprecation_state")
        return self


class FeatureParityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    feature_id: str
    observed_at: datetime
    offline_value: Decimal
    online_value: Decimal
    source_available_at: datetime

    @model_validator(mode="after")
    def valid(self) -> "FeatureParityObservation":
        require_aware(self.observed_at, "observed_at")
        require_aware(self.source_available_at, "source_available_at")
        require_decimal(self.offline_value, "offline_value")
        require_decimal(self.online_value, "online_value")
        return self


class FeatureStoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evaluated_at: datetime
    contracts: list[FeatureContract] = Field(min_length=1, max_length=10_000)
    observations: list[FeatureParityObservation] = Field(
        min_length=1, max_length=MAX_RECORDS
    )
    absolute_tolerance: Decimal = Decimal("0")

    @model_validator(mode="after")
    def valid(self) -> "FeatureStoreRequest":
        require_aware(self.evaluated_at, "evaluated_at")
        require_decimal(
            self.absolute_tolerance, "absolute_tolerance", minimum=Decimal("0")
        )
        ensure_unique((item.feature_id for item in self.contracts), "feature contracts")
        if len(self.contracts) * len(self.observations) > 5_000_000:
            raise ValueError("feature parity workload exceeds limit")
        return self


class FeatureStoreReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    contract_count: int
    observation_count: int
    parity_failures: list[str]
    temporal_leakage: list[str]
    stale_features: list[str]
    blockers: list[str]
    ready_for_human_review: bool
    report_sha256: Annotated[str, Field(pattern=HEX)]
    feature_serving_authority_granted: bool = False
    storage_mutation_performed: bool = False


def evaluate_feature_store(request: FeatureStoreRequest) -> FeatureStoreReport:
    contracts = {item.feature_id: item for item in request.contracts}
    seen: set[str] = set()
    parity: list[str] = []
    leakage: list[str] = []
    stale: list[str] = []
    blockers: list[str] = []
    for obs in request.observations:
        contract = contracts.get(obs.feature_id)
        if contract is None:
            blockers.append(f"unknown_feature:{obs.feature_id}")
            continue
        seen.add(obs.feature_id)
        if abs(obs.offline_value - obs.online_value) > request.absolute_tolerance:
            parity.append(obs.feature_id)
        if obs.source_available_at > obs.observed_at:
            leakage.append(obs.feature_id)
        age = int((request.evaluated_at - obs.observed_at).total_seconds())
        if age < 0 or age > contract.maximum_age_seconds:
            stale.append(obs.feature_id)
    for feature_id, contract in contracts.items():
        if contract.deprecation_state == "active" and feature_id not in seen:
            blockers.append(f"missing_active_feature_evidence:{feature_id}")
    blockers.extend(f"offline_online_mismatch:{value}" for value in sorted(set(parity)))
    blockers.extend(f"point_in_time_leakage:{value}" for value in sorted(set(leakage)))
    blockers.extend(f"feature_stale:{value}" for value in sorted(set(stale)))
    payload = {
        "contract_count": len(contracts),
        "observation_count": len(request.observations),
        "parity_failures": sorted(set(parity)),
        "temporal_leakage": sorted(set(leakage)),
        "stale_features": sorted(set(stale)),
        "blockers": sorted(set(blockers)),
        "ready_for_human_review": not blockers,
    }
    return FeatureStoreReport(**payload, report_sha256=sha256_payload(payload))


class ReplayEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_id: str
    sequence: int = Field(ge=1, le=10**12)
    occurred_at: datetime
    event_type: str
    entity_id: str
    payload: dict[str, Any] = Field(max_length=1_000)
    payload_sha256: Annotated[str, Field(pattern=HEX)]
    previous_event_sha256: Annotated[str, Field(pattern=HEX)] | None = None
    event_sha256: Annotated[str, Field(pattern=HEX)]

    @model_validator(mode="after")
    def valid(self) -> "ReplayEvent":
        require_identifier(self.event_id, "event_id")
        require_identifier(self.entity_id, "entity_id")
        require_aware(self.occurred_at, "occurred_at")
        if self.payload_sha256 != sha256_payload(self.payload):
            raise ValueError("payload hash mismatch")
        body = {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "occurred_at": self.occurred_at,
            "event_type": self.event_type,
            "entity_id": self.entity_id,
            "payload_sha256": self.payload_sha256,
            "previous_event_sha256": self.previous_event_sha256,
        }
        if self.event_sha256 != sha256_payload(body):
            raise ValueError("event hash mismatch")
        return self


class MarketReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    events: list[ReplayEvent] = Field(min_length=1, max_length=MAX_RECORDS)
    expected_final_state_sha256: Annotated[str, Field(pattern=HEX)] | None = None

    @model_validator(mode="after")
    def unique(self) -> "MarketReplayRequest":
        ensure_unique((item.event_id for item in self.events), "event IDs")
        return self


class MarketReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_count: int
    entity_states: dict[str, str]
    final_state_sha256: Annotated[str, Field(pattern=HEX)]
    blockers: list[str]
    replay_equivalent: bool
    report_sha256: Annotated[str, Field(pattern=HEX)]
    execution_authority_granted: bool = False
    state_mutation_performed: bool = False


def replay_market_events(request: MarketReplayRequest) -> MarketReplayReport:
    blockers: list[str] = []
    ordered = sorted(request.events, key=lambda item: item.sequence)
    previous_hash: str | None = None
    previous_time: datetime | None = None
    entity_payloads: dict[str, list[str]] = defaultdict(list)
    for expected_sequence, event in enumerate(ordered, start=1):
        if event.sequence != expected_sequence:
            blockers.append(f"sequence_gap:{expected_sequence}:{event.sequence}")
        if event.previous_event_sha256 != previous_hash:
            blockers.append(f"chain_mismatch:{event.event_id}")
        if previous_time and event.occurred_at < previous_time:
            blockers.append(f"chronology_regression:{event.event_id}")
        entity_payloads[event.entity_id].append(event.event_sha256)
        previous_hash = event.event_sha256
        previous_time = event.occurred_at
    states = {
        entity: sha256_payload(values)
        for entity, values in sorted(entity_payloads.items())
    }
    final_hash = sha256_payload(states)
    if (
        request.expected_final_state_sha256
        and request.expected_final_state_sha256 != final_hash
    ):
        blockers.append("final_state_mismatch")
    payload = {
        "event_count": len(ordered),
        "entity_states": states,
        "final_state_sha256": final_hash,
        "blockers": sorted(set(blockers)),
        "replay_equivalent": not blockers,
    }
    return MarketReplayReport(**payload, report_sha256=sha256_payload(payload))


class FinancingPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instrument_id: str
    quantity: Decimal
    mark_price: Decimal
    borrow_available: Decimal = Decimal("0")
    annual_borrow_rate: Decimal = Decimal("0")
    projected_funding_rate: Decimal = Decimal("0")
    inventory_limit: Decimal | None = None
    recalled: bool = False

    @model_validator(mode="after")
    def valid(self) -> "FinancingPosition":
        require_identifier(self.instrument_id, "instrument_id")
        require_decimal(self.quantity, "quantity")
        require_decimal(
            self.mark_price, "mark_price", minimum=Decimal("0.000000000001")
        )
        require_decimal(self.borrow_available, "borrow_available", minimum=Decimal("0"))
        require_decimal(
            self.annual_borrow_rate,
            "annual_borrow_rate",
            minimum=Decimal("0"),
            maximum=Decimal("100"),
        )
        require_decimal(
            self.projected_funding_rate,
            "projected_funding_rate",
            minimum=Decimal("-100"),
            maximum=Decimal("100"),
        )
        if self.inventory_limit is not None:
            require_decimal(
                self.inventory_limit, "inventory_limit", minimum=Decimal("0")
            )
        return self


class FinancingInventoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    positions: list[FinancingPosition] = Field(min_length=1, max_length=10_000)
    horizon_days: int = Field(default=1, ge=1, le=3660)
    maximum_projected_cost: Decimal = Decimal("1e30")

    @model_validator(mode="after")
    def unique(self) -> "FinancingInventoryRequest":
        ensure_unique(
            (item.instrument_id for item in self.positions), "financing instruments"
        )
        require_decimal(
            self.maximum_projected_cost, "maximum_projected_cost", minimum=Decimal("0")
        )
        return self


class FinancingInventoryReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    projected_borrow_cost: Decimal
    projected_funding_cost: Decimal
    blockers: list[str]
    ready_for_human_review: bool
    report_sha256: Annotated[str, Field(pattern=HEX)]
    borrow_authority_granted: bool = False
    capital_movement_authority_granted: bool = False


def evaluate_financing_inventory(
    request: FinancingInventoryRequest,
) -> FinancingInventoryReport:
    borrow_cost = Decimal("0")
    funding_cost = Decimal("0")
    blockers: list[str] = []
    days = Decimal(request.horizon_days)
    for item in request.positions:
        notional = abs(item.quantity) * item.mark_price
        if item.quantity < 0:
            required = abs(item.quantity)
            if item.recalled:
                blockers.append(f"borrow_recalled:{item.instrument_id}")
            if item.borrow_available < required:
                blockers.append(f"borrow_shortfall:{item.instrument_id}")
            if item.inventory_limit is not None and required > item.inventory_limit:
                blockers.append(f"inventory_limit:{item.instrument_id}")
            borrow_cost += notional * item.annual_borrow_rate * days / Decimal("365")
        funding_cost += notional * item.projected_funding_rate * days
    total = borrow_cost + funding_cost
    if total > request.maximum_projected_cost:
        blockers.append("financing_cost_limit")
    payload = {
        "projected_borrow_cost": borrow_cost,
        "projected_funding_cost": funding_cost,
        "blockers": sorted(set(blockers)),
        "ready_for_human_review": not blockers,
    }
    return FinancingInventoryReport(**payload, report_sha256=sha256_payload(payload))


class AttributionComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    component_id: str
    category: str
    amount: Decimal
    evidence_sha256: Annotated[str, Field(pattern=HEX)]

    @model_validator(mode="after")
    def valid(self) -> "AttributionComponent":
        require_identifier(self.component_id, "component_id")
        require_decimal(self.amount, "amount")
        return self


class AttributionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    accounting_pnl: Decimal
    components: list[AttributionComponent] = Field(min_length=1, max_length=MAX_RECORDS)
    tolerance: Decimal = Decimal("0.00000001")

    @model_validator(mode="after")
    def valid(self) -> "AttributionRequest":
        require_decimal(self.accounting_pnl, "accounting_pnl")
        require_decimal(self.tolerance, "tolerance", minimum=Decimal("0"))
        ensure_unique(
            (item.component_id for item in self.components), "attribution components"
        )
        return self


class AttributionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    attributed_pnl: Decimal
    residual: Decimal
    by_category: dict[str, Decimal]
    blockers: list[str]
    reconciled: bool
    report_sha256: Annotated[str, Field(pattern=HEX)]
    accounting_mutation_performed: bool = False


def attribute_performance(request: AttributionRequest) -> AttributionReport:
    by_category: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for item in request.components:
        by_category[item.category] += item.amount
    attributed = sum(by_category.values(), Decimal("0"))
    residual = request.accounting_pnl - attributed
    blockers = (
        [] if abs(residual) <= request.tolerance else ["attribution_does_not_reconcile"]
    )
    payload = {
        "attributed_pnl": attributed,
        "residual": residual,
        "by_category": dict(sorted(by_category.items())),
        "blockers": blockers,
        "reconciled": not blockers,
    }
    return AttributionReport(**payload, report_sha256=sha256_payload(payload))


class KnowledgeNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    node_id: str
    kind: str
    evidence_sha256: Annotated[str, Field(pattern=HEX)]
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    valid_from: datetime
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def valid(self) -> "KnowledgeNode":
        require_identifier(self.node_id, "node_id")
        require_aware(self.valid_from, "valid_from")
        if self.valid_until:
            require_aware(self.valid_until, "valid_until")
            if self.valid_until <= self.valid_from:
                raise ValueError("valid_until must follow valid_from")
        return self


class KnowledgeEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_id: str
    target_id: str
    relationship: str


class KnowledgeImpactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    evaluated_at: datetime
    nodes: list[KnowledgeNode] = Field(min_length=1, max_length=100_000)
    edges: list[KnowledgeEdge] = Field(max_length=500_000)
    roots: list[str] = Field(min_length=1, max_length=10_000)
    maximum_nodes: int = Field(default=10_000, ge=1, le=100_000)

    @model_validator(mode="after")
    def valid(self) -> "KnowledgeImpactRequest":
        require_aware(self.evaluated_at, "evaluated_at")
        ensure_unique((item.node_id for item in self.nodes), "knowledge node IDs")
        ensure_unique(self.roots, "roots")
        if len(self.nodes) + len(self.edges) > 600_000:
            raise ValueError("knowledge graph workload exceeds limit")
        return self


class KnowledgeImpactReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    impacted_nodes: list[str]
    stale_nodes: list[str]
    missing_roots: list[str]
    blockers: list[str]
    report_sha256: Annotated[str, Field(pattern=HEX)]
    graph_mutation_performed: bool = False


def analyze_knowledge_impact(request: KnowledgeImpactRequest) -> KnowledgeImpactReport:
    nodes = {item.node_id: item for item in request.nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    blockers: list[str] = []
    for edge in request.edges:
        if edge.source_id not in nodes or edge.target_id not in nodes:
            blockers.append(f"dangling_edge:{edge.source_id}:{edge.target_id}")
            continue
        outgoing[edge.source_id].append(edge.target_id)
    missing = sorted(root for root in request.roots if root not in nodes)
    queue = deque(root for root in request.roots if root in nodes)
    seen: set[str] = set()
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        if len(seen) >= request.maximum_nodes:
            blockers.append("knowledge_traversal_limit")
            break
        seen.add(current)
        queue.extend(sorted(outgoing.get(current, ())))
    stale = sorted(
        item.node_id
        for item in nodes.values()
        if item.valid_until is not None and item.valid_until < request.evaluated_at
    )
    blockers.extend(f"missing_root:{item}" for item in missing)
    payload = {
        "impacted_nodes": sorted(seen),
        "stale_nodes": stale,
        "missing_roots": missing,
        "blockers": sorted(set(blockers)),
    }
    return KnowledgeImpactReport(**payload, report_sha256=sha256_payload(payload))


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step_id: str
    depends_on: list[str] = Field(default_factory=list, max_length=1_000)
    idempotency_key: str
    maximum_attempts: int = Field(default=1, ge=1, le=100)
    compensation_step_id: str | None = None
    human_approval_required: bool = False

    @model_validator(mode="after")
    def valid(self) -> "WorkflowStep":
        require_identifier(self.step_id, "step_id")
        require_identifier(self.idempotency_key, "idempotency_key")
        ensure_unique(self.depends_on, "step dependencies")
        return self


class WorkflowAssuranceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=10_000)
    completed_steps: list[str] = Field(default_factory=list, max_length=10_000)
    approved_steps: list[str] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def valid(self) -> "WorkflowAssuranceRequest":
        ensure_unique((item.step_id for item in self.steps), "workflow step IDs")
        ensure_unique(
            (item.idempotency_key for item in self.steps), "workflow idempotency keys"
        )
        ensure_unique(self.completed_steps, "completed_steps")
        ensure_unique(self.approved_steps, "approved_steps")
        return self


class WorkflowAssuranceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    topological_order: list[str]
    runnable_steps: list[str]
    compensated_steps: list[str]
    blockers: list[str]
    ready_for_human_review: bool
    report_sha256: Annotated[str, Field(pattern=HEX)]
    task_execution_performed: bool = False
    governance_authority_granted: bool = False


def assure_workflow(request: WorkflowAssuranceRequest) -> WorkflowAssuranceReport:
    steps = {item.step_id: item for item in request.steps}
    indegree = {step_id: 0 for step_id in steps}
    outgoing: dict[str, list[str]] = defaultdict(list)
    blockers: list[str] = []
    for step in steps.values():
        for dep in step.depends_on:
            if dep not in steps:
                blockers.append(f"missing_dependency:{step.step_id}:{dep}")
                continue
            indegree[step.step_id] += 1
            outgoing[dep].append(step.step_id)
        if step.compensation_step_id and step.compensation_step_id not in steps:
            blockers.append(f"missing_compensation:{step.step_id}")
    queue = deque(
        sorted(step_id for step_id, degree in indegree.items() if degree == 0)
    )
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order.append(current)
        for target in sorted(outgoing.get(current, ())):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(order) != len(steps):
        blockers.append("workflow_cycle")
    completed = set(request.completed_steps)
    approved = set(request.approved_steps)
    runnable = sorted(
        step.step_id
        for step in steps.values()
        if step.step_id not in completed
        and set(step.depends_on) <= completed
        and (not step.human_approval_required or step.step_id in approved)
    )
    compensation = sorted(
        step.compensation_step_id
        for step in steps.values()
        if step.step_id in completed and step.compensation_step_id
    )
    payload = {
        "topological_order": order,
        "runnable_steps": runnable,
        "compensated_steps": compensation,
        "blockers": sorted(set(blockers)),
        "ready_for_human_review": not blockers,
    }
    return WorkflowAssuranceReport(**payload, report_sha256=sha256_payload(payload))


class RuntimeSurface(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    surface_id: str
    kind: str
    executed: bool
    negative_path_executed: bool
    recovery_path_executed: bool
    evidence_sha256: Annotated[str, Field(pattern=HEX)]


class MutationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mutation_id: str
    invariant_id: str
    detected: bool
    evidence_sha256: Annotated[str, Field(pattern=HEX)]


class CoverageAssuranceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    runtime_surfaces: list[RuntimeSurface] = Field(min_length=1, max_length=MAX_RECORDS)
    mutations: list[MutationCase] = Field(min_length=1, max_length=MAX_RECORDS)
    minimum_runtime_ratio: Decimal = Field(
        default=Decimal("1"), ge=Decimal("0"), le=Decimal("1")
    )
    minimum_mutation_score: Decimal = Field(
        default=Decimal("1"), ge=Decimal("0"), le=Decimal("1")
    )
    minimum_negative_path_ratio: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), le=Decimal("1")
    )
    minimum_recovery_path_ratio: Decimal = Field(
        default=Decimal("0"), ge=Decimal("0"), le=Decimal("1")
    )

    @model_validator(mode="after")
    def unique(self) -> "CoverageAssuranceRequest":
        ensure_unique(
            (item.surface_id for item in self.runtime_surfaces), "runtime surface IDs"
        )
        ensure_unique((item.mutation_id for item in self.mutations), "mutation IDs")
        return self


class CoverageAssuranceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    runtime_ratio: Decimal
    negative_path_ratio: Decimal
    recovery_path_ratio: Decimal
    mutation_score: Decimal
    uncovered_surfaces: list[str]
    surviving_mutations: list[str]
    blockers: list[str]
    ready_for_human_review: bool
    report_sha256: Annotated[str, Field(pattern=HEX)]
    mutation_performed: bool = False


def evaluate_dynamic_and_mutation_coverage(
    request: CoverageAssuranceRequest,
) -> CoverageAssuranceReport:
    total = Decimal(len(request.runtime_surfaces))
    runtime = Decimal(sum(item.executed for item in request.runtime_surfaces)) / total
    negative = (
        Decimal(sum(item.negative_path_executed for item in request.runtime_surfaces))
        / total
    )
    recovery = (
        Decimal(sum(item.recovery_path_executed for item in request.runtime_surfaces))
        / total
    )
    mutation_total = Decimal(len(request.mutations))
    mutation_score = (
        Decimal(sum(item.detected for item in request.mutations)) / mutation_total
    )
    uncovered = sorted(
        item.surface_id for item in request.runtime_surfaces if not item.executed
    )
    surviving = sorted(
        item.mutation_id for item in request.mutations if not item.detected
    )
    blockers: list[str] = []
    if runtime < request.minimum_runtime_ratio:
        blockers.append("runtime_coverage_below_threshold")
    if mutation_score < request.minimum_mutation_score:
        blockers.append("mutation_score_below_threshold")
    if negative < request.minimum_negative_path_ratio:
        blockers.append("negative_path_coverage_below_threshold")
    if recovery < request.minimum_recovery_path_ratio:
        blockers.append("recovery_path_coverage_below_threshold")
    payload = {
        "runtime_ratio": runtime,
        "negative_path_ratio": negative,
        "recovery_path_ratio": recovery,
        "mutation_score": mutation_score,
        "uncovered_surfaces": uncovered,
        "surviving_mutations": surviving,
        "blockers": blockers,
        "ready_for_human_review": not blockers,
    }
    return CoverageAssuranceReport(**payload, report_sha256=sha256_payload(payload))


class DonorSaturationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    donor: str
    accessible_surface_count: int = Field(ge=1)
    accounted_surface_count: int = Field(ge=0)
    public_symbol_count: int = Field(ge=0)
    mapped_symbol_count: int = Field(ge=0)
    workflow_count: int = Field(ge=0)
    mapped_workflow_count: int = Field(ge=0)
    benchmark_case_count: int = Field(ge=0)
    runtime_benchmark_case_count: int = Field(default=0, ge=0)
    unresolved_high_risk_units: int = Field(ge=0)
    residual_new_units_last_pass: int = Field(ge=0)
    history_available: bool
    issue_pr_history_available: bool
    evidence_sha256: Annotated[str, Field(pattern=HEX)]


class SaturationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_donors: list[str] = Field(min_length=1, max_length=128)
    evidence: list[DonorSaturationEvidence] = Field(min_length=1, max_length=128)
    benchmarks: BehaviouralBenchmarkReport
    feature_store: FeatureStoreReport
    replay: MarketReplayReport
    financing: FinancingInventoryReport
    attribution: AttributionReport
    knowledge: KnowledgeImpactReport
    workflow: WorkflowAssuranceReport
    coverage: CoverageAssuranceReport
    require_history: bool = False
    require_issue_pr_history: bool = False
    require_runtime_benchmarks: bool = False

    @model_validator(mode="after")
    def valid(self) -> "SaturationRequest":
        ensure_unique(self.expected_donors, "expected_donors")
        ensure_unique((item.donor for item in self.evidence), "donor evidence")
        return self


class SaturationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    donor_completion: dict[str, Decimal]
    blockers: list[str]
    limitations: list[str]
    saturation_reached: bool
    completion_ratio: Decimal
    report_sha256: Annotated[str, Field(pattern=HEX)]
    trading_authority_granted: bool = False
    deployment_authority_granted: bool = False
    legal_compliance_certified: bool = False


def evaluate_convergence_saturation(request: SaturationRequest) -> SaturationReport:
    evidence = {item.donor: item for item in request.evidence}
    blockers: list[str] = []
    limitations: list[str] = []
    completion: dict[str, Decimal] = {}
    for donor in request.expected_donors:
        item = evidence.get(donor)
        if item is None:
            blockers.append(f"donor_evidence_missing:{donor}")
            completion[donor] = Decimal("0")
            continue
        surfaces = Decimal(item.accounted_surface_count) / Decimal(
            item.accessible_surface_count
        )
        symbols = (
            Decimal("1")
            if item.public_symbol_count == 0
            else Decimal(item.mapped_symbol_count) / Decimal(item.public_symbol_count)
        )
        workflows = (
            Decimal("1")
            if item.workflow_count == 0
            else Decimal(item.mapped_workflow_count) / Decimal(item.workflow_count)
        )
        score = min(
            Decimal("1"),
            max(Decimal("0"), (surfaces + symbols + workflows) / Decimal("3")),
        )
        completion[donor] = score
        if item.accounted_surface_count != item.accessible_surface_count:
            blockers.append(f"surface_gap:{donor}")
        if item.mapped_symbol_count != item.public_symbol_count:
            blockers.append(f"symbol_gap:{donor}")
        if item.mapped_workflow_count != item.workflow_count:
            blockers.append(f"workflow_gap:{donor}")
        if item.benchmark_case_count < 1:
            blockers.append(f"benchmark_gap:{donor}")
        if request.require_runtime_benchmarks and item.runtime_benchmark_case_count < 1:
            blockers.append(f"runtime_benchmark_gap:{donor}")
        elif item.runtime_benchmark_case_count < 1:
            limitations.append(f"runtime_benchmark_unavailable:{donor}")
        if item.unresolved_high_risk_units:
            blockers.append(f"unresolved_high_risk:{donor}")
        if item.residual_new_units_last_pass:
            blockers.append(f"residual_new_value:{donor}")
        if request.require_history and not item.history_available:
            blockers.append(f"history_unavailable:{donor}")
        elif not item.history_available:
            limitations.append(f"history_unavailable:{donor}")
        if request.require_issue_pr_history and not item.issue_pr_history_available:
            blockers.append(f"issue_pr_history_unavailable:{donor}")
        elif not item.issue_pr_history_available:
            limitations.append(f"issue_pr_history_unavailable:{donor}")
    reports = {
        "behavioural_benchmarks": request.benchmarks,
        "feature_store": request.feature_store,
        "market_replay": request.replay,
        "financing_inventory": request.financing,
        "performance_attribution": request.attribution,
        "knowledge_impact": request.knowledge,
        "workflow_assurance": request.workflow,
        "dynamic_mutation_coverage": request.coverage,
    }
    for name, report in reports.items():
        data = report.model_dump(mode="python")
        if data.get("blockers"):
            blockers.append(f"plane_blocked:{name}")
        for field in (
            "execution_authority_granted",
            "feature_serving_authority_granted",
            "storage_mutation_performed",
            "state_mutation_performed",
            "borrow_authority_granted",
            "capital_movement_authority_granted",
            "accounting_mutation_performed",
            "graph_mutation_performed",
            "task_execution_performed",
            "governance_authority_granted",
            "mutation_performed",
        ):
            if data.get(field) is True:
                blockers.append(f"authority_violation:{name}:{field}")
    ratio = sum(completion.values(), Decimal("0")) / Decimal(
        len(request.expected_donors)
    )
    payload = {
        "donor_completion": dict(sorted(completion.items())),
        "blockers": sorted(set(blockers)),
        "limitations": sorted(set(limitations)),
        "saturation_reached": not blockers,
        "completion_ratio": ratio,
    }
    return SaturationReport(**payload, report_sha256=sha256_payload(payload))
