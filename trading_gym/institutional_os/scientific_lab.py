"""Scientific trading-lab reproduction protocols and strategy genomes."""
from __future__ import annotations

from datetime import datetime
import math
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.common.security import bounded_json_dumps, redact_secrets

from .base import require_aware, sha256_payload

MAX_GENOME_PAYLOAD_BYTES = 2 * 1024 * 1024


class ReproductionArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: Annotated[str, Field(min_length=2, max_length=80)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    independently_verified: bool


class BenchmarkResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_id: Annotated[str, Field(min_length=2, max_length=128)]
    metric: Annotated[str, Field(min_length=1, max_length=120)]
    value: float
    higher_is_better: bool
    sample_count: Annotated[int, Field(ge=1, le=10**12)]
    uncertainty: float = Field(ge=0.0, le=1e30)

    @model_validator(mode="after")
    def validate_numeric(self) -> "BenchmarkResult":
        if not math.isfinite(self.value) or not math.isfinite(self.uncertainty):
            raise ValueError("benchmark values must be finite")
        return self


class StrategyGene(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gene_id: Annotated[str, Field(min_length=2, max_length=128)]
    category: Annotated[str, Field(pattern=r"^(indicator|rule|risk|execution|parameter)$")]
    value: Any
    mutable: bool = True
    allowed_values: list[Any] = Field(default_factory=list, max_length=10_000)


class GenomeOperation(str, Enum):
    NONE = "none"
    MUTATE = "mutate"
    CROSSOVER = "crossover"


class ScientificLabRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    study_id: Annotated[str, Field(min_length=2, max_length=128)]
    protocol_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    artifacts: list[ReproductionArtifact] = Field(max_length=10_000)
    required_artifact_types: list[Annotated[str, Field(min_length=2, max_length=80)]] = Field(max_length=100)
    benchmarks: list[BenchmarkResult] = Field(max_length=100_000)
    baseline_benchmarks: list[BenchmarkResult] = Field(default_factory=list, max_length=100_000)
    genome: list[StrategyGene] = Field(max_length=10_000)
    secondary_genome: list[StrategyGene] = Field(default_factory=list, max_length=10_000)
    operation: GenomeOperation = GenomeOperation.NONE
    mutation_gene_id: str | None = Field(default=None, min_length=2, max_length=128)
    mutation_value: Any = None
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_request(self) -> "ScientificLabRequest":
        require_aware(self.evaluated_at, "evaluated_at")
        ids = [item.gene_id for item in self.genome]
        if len(ids) != len(set(ids)):
            raise ValueError("strategy gene IDs must be unique")
        if len(self.required_artifact_types) != len(set(self.required_artifact_types)):
            raise ValueError("required artifact types must be unique")
        artifact_keys = [(item.artifact_type, item.sha256) for item in self.artifacts]
        if len(artifact_keys) != len(set(artifact_keys)):
            raise ValueError("reproduction artifacts must be unique")
        benchmark_keys = [(item.benchmark_id, item.metric) for item in self.benchmarks]
        if len(benchmark_keys) != len(set(benchmark_keys)):
            raise ValueError("benchmark results must be unique")
        baseline_keys = [(item.benchmark_id, item.metric) for item in self.baseline_benchmarks]
        if len(baseline_keys) != len(set(baseline_keys)):
            raise ValueError("baseline benchmark results must be unique")
        baseline = {(item.benchmark_id, item.metric): item for item in self.baseline_benchmarks}
        for item in self.benchmarks:
            previous = baseline.get((item.benchmark_id, item.metric))
            if previous is not None and previous.higher_is_better != item.higher_is_better:
                raise ValueError("benchmark direction must match its baseline")
        genome_payload = [item.model_dump(mode="json") for item in self.genome]
        secondary_payload = [item.model_dump(mode="json") for item in self.secondary_genome]
        arbitrary_payload = {"genome": genome_payload, "secondary_genome": secondary_payload, "mutation_value": self.mutation_value}
        if redact_secrets(arbitrary_payload) != arbitrary_payload:
            raise ValueError("strategy genome contains credential-like fields")
        bounded_json_dumps(arbitrary_payload, max_bytes=MAX_GENOME_PAYLOAD_BYTES)
        if self.operation == GenomeOperation.CROSSOVER:
            secondary = [item.gene_id for item in self.secondary_genome]
            if secondary != ids:
                raise ValueError("crossover genomes must have identical ordered gene IDs")
        return self


class ScientificLabReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    study_id: str
    missing_artifact_types: list[str]
    unverified_artifact_types: list[str]
    benchmark_deltas: dict[str, float]
    proposed_genome: list[StrategyGene]
    genome_sha256: str
    blockers: list[str]
    warnings: list[str]
    ready_for_scientific_review: bool
    report_sha256: str
    leaderboard_publication_authority_granted: bool = False
    strategy_registration_authority_granted: bool = False
    mutation_performed: bool = False


def evaluate_scientific_lab(request: ScientificLabRequest) -> ScientificLabReport:
    available = {item.artifact_type for item in request.artifacts}
    missing = sorted(set(request.required_artifact_types) - available)
    unverified = sorted(item.artifact_type for item in request.artifacts if not item.independently_verified)
    blockers: list[str] = []
    warnings: list[str] = []
    if missing:
        blockers.append("reproduction_artifacts_missing")
    if unverified:
        blockers.append("reproduction_artifacts_unverified")
    baseline = {(item.benchmark_id, item.metric): item for item in request.baseline_benchmarks}
    deltas: dict[str, float] = {}
    for item in request.benchmarks:
        key = (item.benchmark_id, item.metric)
        previous = baseline.get(key)
        if previous is None:
            warnings.append(f"benchmark_without_baseline:{item.benchmark_id}:{item.metric}")
            continue
        raw = item.value - previous.value
        deltas[f"{item.benchmark_id}:{item.metric}"] = raw if item.higher_is_better else -raw
        if item.sample_count < 30:
            warnings.append(f"small_benchmark_sample:{item.benchmark_id}:{item.metric}")
    proposed = list(request.genome)
    if request.operation == GenomeOperation.MUTATE:
        found = False
        changed = []
        for gene in proposed:
            if gene.gene_id != request.mutation_gene_id:
                changed.append(gene)
                continue
            found = True
            if not gene.mutable:
                blockers.append("immutable_gene")
                changed.append(gene)
                continue
            if gene.allowed_values and request.mutation_value not in gene.allowed_values:
                blockers.append("mutation_value_not_allowed")
                changed.append(gene)
                continue
            changed.append(gene.model_copy(update={"value": request.mutation_value}))
        if not found:
            blockers.append("mutation_gene_missing")
        proposed = changed
    elif request.operation == GenomeOperation.CROSSOVER:
        proposed = [
            first if index % 2 == 0 else second
            for index, (first, second) in enumerate(zip(request.genome, request.secondary_genome))
        ]
    genome_payload = [item.model_dump(mode="json") for item in proposed]
    genome_hash = sha256_payload(genome_payload)
    payload = {
        "study_id": request.study_id,
        "missing_artifact_types": missing,
        "unverified_artifact_types": unverified,
        "benchmark_deltas": dict(sorted(deltas.items())),
        "proposed_genome": genome_payload,
        "genome_sha256": genome_hash,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "ready_for_scientific_review": not blockers,
    }
    return ScientificLabReport(**payload, report_sha256=sha256_payload(payload))
