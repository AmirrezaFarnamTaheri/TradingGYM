"""Unified market-data lake, quality, and point-in-time feature-store evidence."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import require_aware, sha256_payload

MAX_DATA_QUALITY_CELLS = 5_000_000
MAX_FEATURE_LINEAGE_LINKS = 1_000_000


class DataDomain(str, Enum):
    PRICES = "prices"
    TRADES = "trades"
    ORDERBOOKS = "orderbooks"
    NEWS = "news"
    SOCIAL = "social"
    MACRO = "macro"
    ONCHAIN = "on-chain"
    ALTERNATIVE = "alternative"


class DataPartition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: Annotated[str, Field(min_length=2, max_length=128)]
    domain: DataDomain
    partition_key: Annotated[str, Field(min_length=2, max_length=512)]
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    row_count: Annotated[int, Field(ge=0, le=10**12)]
    minimum_time: datetime
    maximum_time: datetime
    schema_version: Annotated[str, Field(min_length=1, max_length=80)]
    provider_id: Annotated[str, Field(min_length=2, max_length=120)]

    @model_validator(mode="after")
    def validate_times(self) -> "DataPartition":
        minimum = require_aware(self.minimum_time, "minimum_time")
        maximum = require_aware(self.maximum_time, "maximum_time")
        if maximum < minimum:
            raise ValueError("partition maximum_time precedes minimum_time")
        return self


class QualityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: Annotated[str, Field(min_length=2, max_length=128)]
    timestamp: datetime
    values: dict[str, float] = Field(max_length=1000)
    missing_fields: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=1000
    )

    @model_validator(mode="after")
    def validate_values(self) -> "QualityObservation":
        require_aware(self.timestamp, "timestamp")
        if any(not math.isfinite(value) for value in self.values.values()):
            raise ValueError("quality observations must be finite")
        return self


class VendorComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: Annotated[str, Field(min_length=1, max_length=120)]
    primary_value: float
    peer_values: list[float] = Field(min_length=1, max_length=1000)
    maximum_relative_deviation: float = Field(default=0.01, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def validate_values(self) -> "VendorComparison":
        if not math.isfinite(self.primary_value) or any(
            not math.isfinite(item) for item in self.peer_values
        ):
            raise ValueError("vendor comparison values must be finite")
        return self


class FeatureDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature_id: Annotated[str, Field(min_length=2, max_length=128)]
    version: Annotated[str, Field(min_length=1, max_length=80)]
    source_dataset_ids: list[Annotated[str, Field(min_length=2, max_length=128)]] = (
        Field(min_length=1, max_length=1000)
    )
    transformation_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    event_time_field: Annotated[str, Field(min_length=1, max_length=120)]
    availability_delay_seconds: Annotated[int, Field(ge=0, le=10 * 365 * 86400)] = 0
    owner: Annotated[str, Field(min_length=2, max_length=120)]
    online_enabled: bool = False
    offline_enabled: bool = True

    @model_validator(mode="after")
    def validate_serving_modes(self) -> "FeatureDefinition":
        if not self.online_enabled and not self.offline_enabled:
            raise ValueError("feature must be enabled for at least one serving mode")
        return self


class FeatureMaterialisation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature_id: Annotated[str, Field(min_length=2, max_length=128)]
    feature_version: Annotated[str, Field(min_length=1, max_length=80)]
    event_time: datetime
    available_at: datetime
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def validate_times(self) -> "FeatureMaterialisation":
        event_time = require_aware(self.event_time, "event_time")
        available = require_aware(self.available_at, "available_at")
        if available < event_time:
            raise ValueError("feature cannot be available before event time")
        return self


class DataPlatformRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    partitions: list[DataPartition] = Field(max_length=1_000_000)
    quality_observations: list[QualityObservation] = Field(
        default_factory=list, max_length=1_000_000
    )
    vendor_comparisons: list[VendorComparison] = Field(
        default_factory=list, max_length=100_000
    )
    feature_definitions: list[FeatureDefinition] = Field(
        default_factory=list, max_length=100_000
    )
    feature_materialisations: list[FeatureMaterialisation] = Field(
        default_factory=list, max_length=1_000_000
    )
    evaluated_at: datetime
    maximum_staleness_seconds: Annotated[int, Field(ge=1, le=10 * 365 * 86400)] = 86400
    maximum_missing_fraction: float = Field(default=0.01, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_request(self) -> "DataPlatformRequest":
        require_aware(self.evaluated_at, "evaluated_at")
        keys = [(item.dataset_id, item.partition_key) for item in self.partitions]
        if len(keys) != len(set(keys)):
            raise ValueError("dataset partition keys must be unique")
        definitions = [
            (item.feature_id, item.version) for item in self.feature_definitions
        ]
        if len(definitions) != len(set(definitions)):
            raise ValueError("feature definition versions must be unique")
        comparison_fields = [item.field for item in self.vendor_comparisons]
        if len(comparison_fields) != len(set(comparison_fields)):
            raise ValueError("vendor comparison fields must be unique")
        materialisation_keys = [
            (item.feature_id, item.feature_version, item.event_time)
            for item in self.feature_materialisations
        ]
        if len(materialisation_keys) != len(set(materialisation_keys)):
            raise ValueError(
                "feature materialisations must be unique by feature version and event time"
            )
        for definition in self.feature_definitions:
            if len(definition.source_dataset_ids) != len(
                set(definition.source_dataset_ids)
            ):
                raise ValueError("feature source dataset IDs must be unique")
        quality_cells = sum(
            len(item.values) + len(item.missing_fields)
            for item in self.quality_observations
        )
        if quality_cells > MAX_DATA_QUALITY_CELLS:
            raise ValueError("data-quality work budget exceeded")
        if (
            sum(len(item.source_dataset_ids) for item in self.feature_definitions)
            > MAX_FEATURE_LINEAGE_LINKS
        ):
            raise ValueError("feature-lineage work budget exceeded")
        return self


class DataPlatformReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain_counts: dict[str, int]
    dataset_row_counts: dict[str, int]
    stale_partitions: list[str]
    quality_failures: list[str]
    vendor_disagreements: list[str]
    feature_lineage: dict[str, list[str]]
    point_in_time_failures: list[str]
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    storage_mutation_performed: bool = False
    feature_serving_authority_granted: bool = False


def evaluate_data_platform(request: DataPlatformRequest) -> DataPlatformReport:
    domain_counts: dict[str, int] = defaultdict(int)
    rows: dict[str, int] = defaultdict(int)
    stale: list[str] = []
    quality_failures: list[str] = []
    disagreements: list[str] = []
    pit_failures: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []
    datasets = {item.dataset_id for item in request.partitions}
    partition_hashes: dict[str, set[str]] = defaultdict(set)
    partition_counts: dict[str, int] = defaultdict(int)
    for item in request.partitions:
        domain_counts[item.domain.value] += 1
        rows[item.dataset_id] += item.row_count
        partition_hashes[item.dataset_id].add(item.content_sha256)
        partition_counts[item.dataset_id] += 1
        age = (request.evaluated_at - item.maximum_time).total_seconds()
        if age < 0:
            blockers.append(f"future_partition:{item.dataset_id}:{item.partition_key}")
        elif age > request.maximum_staleness_seconds:
            stale.append(f"{item.dataset_id}:{item.partition_key}")
        if item.row_count == 0:
            warnings.append(f"empty_partition:{item.dataset_id}:{item.partition_key}")
    by_dataset: dict[str, list[QualityObservation]] = defaultdict(list)
    for item in request.quality_observations:
        if item.dataset_id not in datasets:
            quality_failures.append(f"unknown_quality_dataset:{item.dataset_id}")
        if item.timestamp > request.evaluated_at:
            quality_failures.append(
                f"future_quality_observation:{item.dataset_id}:{item.timestamp.isoformat()}"
            )
        by_dataset[item.dataset_id].append(item)
    for dataset_id in sorted(datasets):
        observations = by_dataset.get(dataset_id, [])
        if not observations:
            quality_failures.append(f"missing_quality_observations:{dataset_id}")
            continue
        missing = sum(len(item.missing_fields) for item in observations)
        fields = sum(
            max(1, len(item.values) + len(item.missing_fields)) for item in observations
        )
        if missing / fields > request.maximum_missing_fraction:
            quality_failures.append(f"missingness:{dataset_id}")
        times = [item.timestamp for item in observations]
        if times != sorted(times) or len(times) != len(set(times)):
            quality_failures.append(f"chronology:{dataset_id}")
    for item in request.vendor_comparisons:
        median = sorted(item.peer_values)[len(item.peer_values) // 2]
        denominator = max(abs(median), 1e-12)
        if (
            abs(item.primary_value - median) / denominator
            > item.maximum_relative_deviation
        ):
            disagreements.append(item.field)
    duplicate_content = sorted(
        dataset_id
        for dataset_id, hashes in partition_hashes.items()
        if len(hashes) < partition_counts[dataset_id]
    )
    if duplicate_content:
        warnings.extend(
            f"duplicate_partition_content:{item}" for item in duplicate_content
        )
    definitions = {
        (item.feature_id, item.version): item for item in request.feature_definitions
    }
    lineage: dict[str, list[str]] = {}
    for definition in request.feature_definitions:
        key = f"{definition.feature_id}:{definition.version}"
        lineage[key] = sorted(definition.source_dataset_ids)
        missing_sources = sorted(set(definition.source_dataset_ids) - datasets)
        if missing_sources:
            blockers.append(
                f"missing_feature_sources:{key}:{','.join(missing_sources[:10])}"
            )
    for item in request.feature_materialisations:
        definition = definitions.get((item.feature_id, item.feature_version))
        if definition is None:
            pit_failures.append(
                f"missing_definition:{item.feature_id}:{item.feature_version}"
            )
            continue
        if item.available_at > request.evaluated_at:
            pit_failures.append(
                f"future_materialisation:{item.feature_id}:{item.content_sha256}"
            )
        earliest = item.event_time.timestamp() + definition.availability_delay_seconds
        if item.available_at.timestamp() + 1e-9 < earliest:
            pit_failures.append(
                f"availability_delay:{item.feature_id}:{item.content_sha256}"
            )
    if stale:
        blockers.append("stale_partitions")
    if quality_failures:
        blockers.append("data_quality_failed")
    if disagreements:
        blockers.append("vendor_disagreement")
    if pit_failures:
        blockers.append("point_in_time_integrity_failed")
    payload = {
        "domain_counts": dict(sorted(domain_counts.items())),
        "dataset_row_counts": dict(sorted(rows.items())),
        "stale_partitions": sorted(stale),
        "quality_failures": sorted(set(quality_failures)),
        "vendor_disagreements": sorted(set(disagreements)),
        "feature_lineage": dict(sorted(lineage.items())),
        "point_in_time_failures": sorted(set(pit_failures)),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    return DataPlatformReport(**payload, report_sha256=sha256_payload(payload))
