"""Deterministic research coverage, freshness, and citation-quality assessment."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from trading_gym.agent.research.models import EvidenceQuality, ResearchDossier

_QUALITY_WEIGHT = {
    EvidenceQuality.PRIMARY: 1.0,
    EvidenceQuality.AUTHORITATIVE: 0.9,
    EvidenceQuality.REPUTABLE: 0.75,
    EvidenceQuality.COMMUNITY: 0.45,
    EvidenceQuality.UNKNOWN: 0.2,
}


class ResearchQualityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_count: int
    cited_evidence_count: int
    coverage_ratio: float
    mean_quality: float
    fresh_ratio: float
    duplicated_evidence_ids: list[str]
    uncited_evidence_ids: list[str]
    missing_sections: list[str]
    warnings: list[str]


def assess_dossier(
    dossier: ResearchDossier,
    *,
    as_of: datetime | None = None,
    freshness_days: int = 30,
) -> ResearchQualityAssessment:
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ids = [item.evidence_id for item in dossier.evidence]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    evidence_by_id = {item.evidence_id: item for item in dossier.evidence}
    cited = {
        evidence_id
        for section in dossier.sections
        for evidence_id in section.evidence_ids
        if evidence_id in evidence_by_id
    }
    uncited = sorted(set(evidence_by_id) - cited)
    count = len(evidence_by_id)
    mean_quality = (
        sum(_QUALITY_WEIGHT[item.quality] for item in evidence_by_id.values()) / count
        if count
        else 0.0
    )
    fresh = 0
    for item in evidence_by_id.values():
        reference = item.published_at or item.observed_at
        reference = (
            reference if reference.tzinfo else reference.replace(tzinfo=timezone.utc)
        )
        if (now - reference.astimezone(timezone.utc)).days <= freshness_days:
            fresh += 1
    missing = [
        section.name for section in dossier.sections if section.status == "missing"
    ]
    warnings: list[str] = []
    if duplicates:
        warnings.append("duplicate evidence identifiers")
    if uncited:
        warnings.append("dossier contains uncited evidence")
    if missing:
        warnings.append("required research sections are missing")
    if count and mean_quality < 0.5:
        warnings.append("evidence quality is predominantly weak or unknown")
    if count and fresh / count < 0.5:
        warnings.append("most evidence is outside the freshness window")
    return ResearchQualityAssessment(
        evidence_count=count,
        cited_evidence_count=len(cited),
        coverage_ratio=len(cited) / count if count else 0.0,
        mean_quality=mean_quality,
        fresh_ratio=fresh / count if count else 0.0,
        duplicated_evidence_ids=duplicates,
        uncited_evidence_ids=uncited,
        missing_sections=missing,
        warnings=warnings,
    )
