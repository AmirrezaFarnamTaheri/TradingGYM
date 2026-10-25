"""Bounded request contracts shared by convergence API route families."""

from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from typing import Annotated, Any
from pydantic import BaseModel, ConfigDict, Field
from trading_gym.core.events import EventEnvelope
from trading_gym.execution.accounting import (
    FXQuote,
    ObservedAccountState,
    ReconciliationTolerance,
    SettlementObligation,
)
from trading_gym.agent.providers import ProviderConfig
from trading_gym.execution.calibration import (
    CalibrationThresholds,
    ExecutionObservation,
)
from trading_gym.execution.simulation import (
    ExecutionSimulationPolicy,
    ExecutionSimulationRequest,
)
from trading_gym.execution.governance.triggers import TriggerState
from trading_gym.data.lineage import LineageEdgeCreate, LineageNodeCreate
from trading_gym.market import MarketCalendarSpec, DatasetRegistration, InstrumentSpec
from trading_gym.core.monitoring.drift import DriftBaselineCreate
from trading_gym.core.operations import (
    IncidentEvent,
    SignedBuildAttestation,
    SLOObservationWindow,
    ServiceLevelObjective,
)
from trading_gym.execution.orders import OrderEvent, OrderObservation
from trading_gym.rl.performance import LotFill, LotMethod, MarkValue
from trading_gym.execution.portfolio import RebalanceHolding, RebalancePolicy, RebalanceTarget
from trading_gym.agent.research.models import EvidenceItem, ResearchDossier
from trading_gym.agent.research.workspace import WatchlistEntry
from trading_gym.execution.risk.derivatives import DerivativePosition, DerivativeRiskPolicy
from trading_gym.execution.risk.stress import (
    PortfolioStressPolicy,
    StressPosition,
    StressScenario,
)
from trading_gym.agent.strategy_lab.models import StrategySpec


class StrategySaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: StrategySpec
    strategy_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    actor: str = Field(default="api-operator", min_length=1, max_length=120)


class StrategyValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: StrategySpec
    market: str | None = Field(default=None, max_length=80)
    start: datetime | None = None
    end: datetime | None = None


class DossierRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=500)
    required_sections: list[str] | None = Field(default=None, max_length=30)


class ReflectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dossier: ResearchDossier
    claims: list[Annotated[str, Field(max_length=4000)]] = Field(max_length=500)


class PortfolioStressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equity: float = Field(gt=0.0, le=1e30)
    positions: list[StressPosition] = Field(default_factory=list, max_length=100_000)
    scenario: StressScenario
    policy: PortfolioStressPolicy = Field(default_factory=PortfolioStressPolicy)


class ProviderSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: ProviderConfig
    expected_revision: int | None = Field(default=None, ge=0)


class InstrumentSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument: InstrumentSpec
    expected_revision: int | None = Field(default=None, ge=0)


class LeaseRenewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: str = Field(min_length=8, max_length=200)
    fencing_token: int = Field(ge=1)
    now: datetime
    ttl_seconds: int = Field(ge=5, le=86_400)


class LeaseReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: str = Field(min_length=8, max_length=200)
    fencing_token: int = Field(ge=1)
    now: datetime


class EventReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[EventEnvelope] = Field(max_length=1_000_000)
    genesis_hash: str = Field(default="0" * 64, pattern=r"^[0-9a-f]{64}$")
    initial_projection: dict[str, Any] = Field(default_factory=dict)


class BacktestWindowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str = Field(min_length=1, max_length=80)
    timeframe: str = Field(pattern=r"^(1m|3m|5m|15m|30m|1h|4h|1d|1w)$")
    start: datetime
    end: datetime
    warmup_bars: int = Field(default=0, ge=0, le=100_000)


class DatasetSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: DatasetRegistration
    expected_catalog_revision: int | None = Field(default=None, ge=0)


class DatasetQuarantineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class WorkspaceWatchlistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry: WatchlistEntry
    expected_revision: int | None = Field(default=None, ge=0)


class WorkspaceNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=50_000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=500)
    note_id: str | None = Field(default=None, max_length=120)
    expected_revision: int | None = Field(default=None, ge=0)


class WorkspaceReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dossier: ResearchDossier
    label: str = Field(default="", max_length=200)


class ExperimentClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=120)
    accepted_types: set[str] | None = Field(default=None, max_length=20)
    lease_seconds: int = Field(default=120, ge=30, le=3600)


class ExperimentHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(ge=1)
    lease_seconds: int = Field(default=120, ge=30, le=3600)


class ExperimentCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(ge=1)
    result: dict[str, Any] = Field(default_factory=dict)


class ExperimentFailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(ge=1)
    error: str = Field(min_length=1, max_length=4000)
    retryable: bool = False


class ExperimentCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class AccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_cash: dict[str, Decimal] = Field(default_factory=dict, max_length=200)


class AccountValuationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_currency: str = Field(pattern=r"^[A-Z0-9]{2,20}$")
    quotes: list[FXQuote] = Field(default_factory=list, max_length=100_000)
    valued_at: datetime | None = None
    max_fx_age_seconds: int = Field(default=3600, ge=1, le=31_536_000)


class AccountReconciliationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed: ObservedAccountState
    tolerance: ReconciliationTolerance = Field(default_factory=ReconciliationTolerance)


class ExecutionSimulationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: ExecutionSimulationRequest
    policy: ExecutionSimulationPolicy = Field(default_factory=ExecutionSimulationPolicy)


class DriftBaselineSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline: DriftBaselineCreate
    expected_store_revision: int | None = Field(default=None, ge=0)


class SnapshotCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_paths: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        min_length=1, max_length=1000
    )
    snapshot_id: str | None = Field(default=None, max_length=120)


class SLOEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: ServiceLevelObjective
    window: SLOObservationWindow


class OrderAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: OrderEvent
    expected_revision: int = Field(ge=1)


class OrderReconciliationRequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation: OrderObservation


class LotAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fills: list[LotFill] = Field(max_length=1_000_000)
    base_currency: str = Field(min_length=1, max_length=20)
    method: LotMethod = LotMethod.FIFO
    marks: list[MarkValue] = Field(default_factory=list, max_length=100_000)


class ExecutionCalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observations: list[ExecutionObservation] = Field(max_length=1_000_000)
    base_policy: ExecutionSimulationPolicy = Field(
        default_factory=ExecutionSimulationPolicy
    )
    thresholds: CalibrationThresholds = Field(default_factory=CalibrationThresholds)


class LineageNodeSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: LineageNodeCreate
    expected_revision: int | None = Field(default=None, ge=0)


class LineageEdgeSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge: LineageEdgeCreate
    expected_revision: int | None = Field(default=None, ge=0)


class BudgetCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actual_amount: Decimal = Field(ge=0)
    expected_revision: int = Field(ge=1)


class BudgetReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class AttestationVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attestation: SignedBuildAttestation


class MarketSessionInspectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar: MarketCalendarSpec
    observed_at: datetime


class DerivativeRiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equity: Decimal
    free_collateral: Decimal
    positions: list[DerivativePosition] = Field(
        default_factory=list, max_length=100_000
    )
    policy: DerivativeRiskPolicy = Field(default_factory=DerivativeRiskPolicy)


class RebalanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_currency: str = Field(pattern=r"^[A-Z0-9]{2,20}$")
    cash_base: Decimal
    holdings: list[RebalanceHolding] = Field(default_factory=list, max_length=100_000)
    targets: list[RebalanceTarget] = Field(default_factory=list, max_length=100_000)
    policy: RebalancePolicy = Field(default_factory=RebalancePolicy)


class SettlementEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    settled_cash: dict[str, Decimal] = Field(default_factory=dict, max_length=200)
    obligations: list[SettlementObligation] = Field(
        default_factory=list, max_length=1_000_000
    )


class IncidentAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: IncidentEvent
    expected_revision: int = Field(ge=1)


class IncidentReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_ids: list[str] | None = Field(default=None, max_length=100_000)
    evaluated_at: datetime | None = None


class TriggerStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: TriggerState
    expected_revision: int = Field(ge=1)


class TriggerEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_key: str = Field(min_length=1, max_length=200)
    metrics: dict[str, Any] = Field(default_factory=dict)
    event_name: str | None = Field(default=None, max_length=120)
    event_payload: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime | None = None
