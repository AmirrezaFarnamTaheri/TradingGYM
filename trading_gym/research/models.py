"""Evidence-first market research models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class EvidenceQuality(str, Enum):
    PRIMARY = "primary"
    AUTHORITATIVE = "authoritative"
    REPUTABLE = "reputable"
    COMMUNITY = "community"
    UNKNOWN = "unknown"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    source: Annotated[str, Field(min_length=1, max_length=200)]
    url: HttpUrl | None = None
    observed_at: datetime
    published_at: datetime | None = None
    quality: EvidenceQuality = EvidenceQuality.UNKNOWN
    facts: Annotated[list[str], Field(min_length=1, max_length=50)]
    raw_payload_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResearchSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, Field(min_length=1, max_length=120)]
    status: str
    evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    findings: list[str] = Field(default_factory=list, max_length=200)
    limitations: list[str] = Field(default_factory=list, max_length=50)


class ResearchDossier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    subject: Annotated[str, Field(min_length=1, max_length=200)]
    as_of: datetime
    sections: Annotated[list[ResearchSection], Field(min_length=1, max_length=30)]
    evidence: Annotated[list[EvidenceItem], Field(max_length=1000)]
    missing_data: list[str] = Field(default_factory=list, max_length=100)
    caveats: list[str] = Field(default_factory=list, max_length=100)
    disclaimer: str = (
        "Research evidence only; not financial advice or an execution instruction."
    )
