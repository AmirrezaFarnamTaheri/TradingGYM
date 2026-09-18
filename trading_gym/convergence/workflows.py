"""Cross-plane workflows that preserve execution and authority boundaries."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from trading_gym.execution.simulation import ExecutionSimulationPolicy
from trading_gym.experiments import (
    ArtifactReference,
    ExperimentCreate,
    ExperimentRecord,
    ExperimentRepository,
    RunManifest,
    RunManifestCreate,
    RunManifestRepository,
    SeedBundle,
    build_manifest,
    hash_source_tree,
)
from trading_gym.governance.ledger import AuditLedger
from trading_gym.market import InstrumentRegistry
from trading_gym.strategy_lab.models import StrategySpec
from trading_gym.strategy_lab.repository import StrategyRepository
from trading_gym.strategy_lab.validation import (
    StrategyValidationReport,
    validate_strategy_spec,
)


def _sha_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


class BacktestWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: Annotated[str, Field(min_length=1, max_length=120)]
    instrument_identifier: Annotated[str, Field(min_length=1, max_length=120)]
    venue: Annotated[str | None, Field(max_length=40)] = None
    market: Annotated[str, Field(min_length=1, max_length=80)]
    start: datetime
    end: datetime
    train_bars: Annotated[int, Field(ge=50, le=1_000_000)] = 2000
    test_bars: Annotated[int, Field(ge=10, le=500_000)] = 500
    purge_bars: Annotated[int, Field(ge=0, le=100_000)] = 20
    embargo_bars: Annotated[int, Field(ge=0, le=100_000)] = 5
    randomized_baselines: Annotated[int, Field(ge=20, le=10_000)] = 200
    priority: Annotated[int, Field(ge=0, le=100)] = 50
    max_attempts: Annotated[int, Field(ge=1, le=10)] = 2
    idempotency_key: Annotated[str, Field(min_length=8, max_length=200)]
    seeds: SeedBundle = Field(
        default_factory=lambda: SeedBundle(python=0, numpy=0, torch=0, environment=0)
    )
    execution_simulation: ExecutionSimulationPolicy = Field(
        default_factory=ExecutionSimulationPolicy
    )
    dependency_names: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=lambda: ["numpy", "pandas", "pydantic"], max_length=100
    )


class BacktestExperimentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy_id: str
    strategy_revision: int
    strategy_sha256: str
    instrument_id: str
    validation: StrategyValidationReport
    experiment: ExperimentRecord
    manifest: RunManifest
    execution_authority_granted: bool = False


class ConvergenceWorkflowService:
    def __init__(
        self,
        *,
        strategies: StrategyRepository | None = None,
        instruments: InstrumentRegistry | None = None,
        experiments: ExperimentRepository | None = None,
        manifests: RunManifestRepository | None = None,
        ledger: AuditLedger | None = None,
        source_root: Path | None = None,
        source_sha256: str | None = None,
    ) -> None:
        self.strategies = strategies or StrategyRepository()
        self.instruments = instruments or InstrumentRegistry()
        self.experiments = experiments or ExperimentRepository()
        self.manifests = manifests or RunManifestRepository()
        self.ledger = ledger or AuditLedger()
        self.source_root = source_root
        self._source_sha256 = source_sha256

    def source_sha256(self) -> str:
        if self._source_sha256 is None:
            self._source_sha256 = hash_source_tree(self.source_root)
        return self._source_sha256

    @staticmethod
    def _same_manifest(existing: RunManifest, candidate: RunManifest) -> bool:
        return (
            existing.experiment_id == candidate.experiment_id
            and existing.code_sha256 == candidate.code_sha256
            and existing.configuration_sha256 == candidate.configuration_sha256
            and existing.artifacts == candidate.artifacts
            and existing.seeds == candidate.seeds
            and existing.environment == candidate.environment
            and existing.parent_manifest_id == candidate.parent_manifest_id
            and existing.notes == candidate.notes
        )

    def plan_backtest(
        self,
        request: BacktestWorkflowRequest,
        *,
        actor: str,
    ) -> BacktestExperimentPlan:
        actor = actor.strip()
        if not actor or len(actor) > 120:
            raise ValueError("actor must be 1-120 characters")
        record = self.strategies.get(request.strategy_id)
        latest: dict[str, Any] = record["versions"][-1]
        strategy = StrategySpec.model_validate(latest["spec"])
        instrument = self.instruments.resolve(
            request.instrument_identifier,
            venue=request.venue,
        )
        validation = validate_strategy_spec(
            strategy,
            market=request.market,
            start=request.start,
            end=request.end,
        )
        if not validation.valid:
            raise ValueError("strategy/backtest plan failed validation")
        if request.purge_bars < validation.warmup_bars:
            raise ValueError(
                f"purge_bars must be at least the strategy warmup ({validation.warmup_bars})"
            )
        if request.test_bars <= request.embargo_bars:
            raise ValueError("test_bars must exceed embargo_bars")

        configuration = {
            "schema_version": 2,
            "strategy": {
                "strategy_id": request.strategy_id,
                "revision": int(record["revision"]),
                "sha256": latest["sha256"],
                "spec": latest["spec"],
            },
            "instrument": instrument.model_dump(mode="json"),
            "window": {
                "market": request.market,
                "start": request.start.isoformat(),
                "end": request.end.isoformat(),
                "policy": validation.backtest_policy,
            },
            "walk_forward": {
                "train_bars": request.train_bars,
                "test_bars": request.test_bars,
                "purge_bars": request.purge_bars,
                "embargo_bars": request.embargo_bars,
            },
            "multiple_testing": {
                "randomized_baselines": request.randomized_baselines,
                "require_deflated_sharpe": True,
                "require_false_discovery_control": True,
            },
            "execution_simulation": request.execution_simulation.model_dump(
                mode="json"
            ),
            "seeds": request.seeds.model_dump(mode="json"),
            "authority": {
                "mode": "research_only",
                "live_execution_allowed": False,
                "promotion_requires_paper_and_human_approval": True,
            },
        }
        experiment = self.experiments.create(
            ExperimentCreate(
                experiment_type="backtest",
                name=f"{strategy.name} on {instrument.instrument_id}",
                configuration=configuration,
                idempotency_key=request.idempotency_key,
                priority=request.priority,
                max_attempts=request.max_attempts,
            )
        )
        manifest_request = RunManifestCreate(
            experiment_id=experiment.experiment_id,
            code_sha256=self.source_sha256(),
            configuration=configuration,
            artifacts=[
                ArtifactReference(
                    name=request.strategy_id,
                    kind="strategy",
                    sha256=str(latest["sha256"]),
                ),
                ArtifactReference(
                    name=instrument.instrument_id,
                    kind="configuration",
                    sha256=_sha_json(instrument.model_dump(mode="json")),
                ),
            ],
            seeds=request.seeds,
            dependency_names=request.dependency_names,
            notes="Automatically generated by the bounded backtest workflow.",
        )
        candidate = build_manifest(manifest_request)
        existing_manifests = self.manifests.list_for_experiment(
            experiment.experiment_id
        )
        if existing_manifests:
            manifest = existing_manifests[-1]
            if not self._same_manifest(manifest, candidate):
                raise ValueError(
                    "idempotent backtest request conflicts with existing run manifest"
                )
        else:
            manifest = self.manifests.create(manifest_request, actor=actor)

        self.ledger.append(
            event_id=f"experiment:{experiment.experiment_id}:backtest-planned",
            event="workflow.backtest_planned",
            actor=actor,
            subject=experiment.experiment_id,
            details={
                "strategy_id": request.strategy_id,
                "strategy_revision": record["revision"],
                "instrument_id": instrument.instrument_id,
                "manifest_id": manifest.manifest_id,
                "manifest_sha256": manifest.manifest_sha256,
                "execution_authority_granted": False,
            },
        )
        return BacktestExperimentPlan(
            strategy_id=request.strategy_id,
            strategy_revision=int(record["revision"]),
            strategy_sha256=str(latest["sha256"]),
            instrument_id=instrument.instrument_id,
            validation=validation,
            experiment=experiment,
            manifest=manifest,
        )
