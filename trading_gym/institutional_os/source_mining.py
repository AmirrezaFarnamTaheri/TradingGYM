"""Exhaustive semantic-mining evidence for donor archives and historical artefacts."""
from __future__ import annotations

from collections import Counter
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import HEX64_RE, sha256_payload

MAX_SOURCE_MINING_SEMANTIC_UNITS = 5_000_000


class SurfaceDisposition(str, Enum):
    ADOPTED = "adopted"
    ADAPTED = "adapted"
    SYNTHESIZED = "synthesized"
    GUARDRAIL = "guardrail-derived"
    REJECTED = "rejected-with-reason"
    REFERENCE = "reference-only"


class HistoricalSurface(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    donor: Annotated[str, Field(min_length=1, max_length=120)]
    path: Annotated[str, Field(min_length=1, max_length=1024)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    category: Annotated[str, Field(min_length=1, max_length=80)]
    semantic_units: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(max_length=100)
    disposition: SurfaceDisposition
    rationale: Annotated[str, Field(min_length=8, max_length=4000)]
    high_risk: bool = False
    evidence_complete: bool = True


class SourceMiningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_donors: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(min_length=1, max_length=100)
    surfaces: list[HistoricalSurface] = Field(max_length=1_000_000)
    require_high_risk_resolution: bool = True
    minimum_semantic_units_per_donor: Annotated[int, Field(ge=1, le=1_000_000)] = 1

    @model_validator(mode="after")
    def validate_unique_surfaces(self) -> "SourceMiningRequest":
        keys = [(item.donor, item.path) for item in self.surfaces]
        if len(keys) != len(set(keys)):
            raise ValueError("donor/path surfaces must be unique")
        if len(self.expected_donors) != len(set(self.expected_donors)):
            raise ValueError("expected donors must be unique")
        if sum(len(item.semantic_units) for item in self.surfaces) > MAX_SOURCE_MINING_SEMANTIC_UNITS:
            raise ValueError("source-mining semantic-unit budget exceeded")
        return self


class SourceMiningReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    donor_counts: dict[str, int]
    category_counts: dict[str, int]
    disposition_counts: dict[str, int]
    semantic_unit_counts: dict[str, int]
    unresolved_high_risk: list[str]
    blockers: list[str]
    warnings: list[str]
    saturation_reached: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mutation_performed: bool = False
    repository_authority_granted: bool = False


def evaluate_source_mining(request: SourceMiningRequest) -> SourceMiningReport:
    donor_counts: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    dispositions: Counter[str] = Counter()
    semantic_counts: Counter[str] = Counter()
    unresolved: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []
    expected = set(request.expected_donors)
    for item in request.surfaces:
        if item.donor not in expected:
            warnings.append(f"unexpected_donor:{item.donor}")
        donor_counts[item.donor] += 1
        categories[item.category] += 1
        dispositions[item.disposition.value] += 1
        semantic_counts[item.donor] += len(item.semantic_units)
        if item.high_risk and not item.evidence_complete:
            unresolved.append(f"{item.donor}:{item.path}")
        if not HEX64_RE.fullmatch(item.sha256):
            blockers.append(f"invalid_hash:{item.donor}:{item.path}")
    for donor in sorted(expected):
        if donor_counts[donor] == 0:
            blockers.append(f"missing_donor:{donor}")
        if semantic_counts[donor] < request.minimum_semantic_units_per_donor:
            blockers.append(f"insufficient_semantic_units:{donor}:{semantic_counts[donor]}")
    if request.require_high_risk_resolution and unresolved:
        blockers.append("unresolved_high_risk_surfaces")
    payload = {
        "donor_counts": dict(sorted(donor_counts.items())),
        "category_counts": dict(sorted(categories.items())),
        "disposition_counts": dict(sorted(dispositions.items())),
        "semantic_unit_counts": dict(sorted(semantic_counts.items())),
        "unresolved_high_risk": sorted(unresolved),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "saturation_reached": not blockers,
    }
    return SourceMiningReport(**payload, report_sha256=sha256_payload(payload))
