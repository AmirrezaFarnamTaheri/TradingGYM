"""Claim-level citation and contradiction audit."""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from trading_gym.agent.research.models import ResearchDossier

_CITATION = re.compile(r"\[([A-Za-z0-9_.:-]{1,120})\]")


@dataclass(frozen=True)
class ReflectionFinding:
    severity: str
    code: str
    message: str
    claim: str


def audit_claims(claims: list[str], dossier: ResearchDossier) -> dict[str, object]:
    evidence_ids = {item.evidence_id for item in dossier.evidence}
    findings: list[ReflectionFinding] = []
    for claim in claims[:500]:
        citations = _CITATION.findall(claim)
        if not citations:
            findings.append(
                ReflectionFinding(
                    "high",
                    "unsupported_claim",
                    "Claim has no evidence citation.",
                    claim,
                )
            )
            continue
        unknown = sorted(set(citations) - evidence_ids)
        if unknown:
            findings.append(
                ReflectionFinding(
                    "high",
                    "unknown_citation",
                    f"Unknown evidence IDs: {', '.join(unknown)}",
                    claim,
                )
            )
        lowered = claim.lower()
        for missing in dossier.missing_data:
            if missing.lower() in lowered and any(
                token in lowered for token in ("none", "no ", "absent", "did not")
            ):
                findings.append(
                    ReflectionFinding(
                        "medium",
                        "absence_from_missing_data",
                        f"Claim treats missing {missing} evidence as evidence of absence.",
                        claim,
                    )
                )
    return {
        "valid": not any(item.severity == "high" for item in findings),
        "claims_checked": min(len(claims), 500),
        "findings": [asdict(item) for item in findings],
    }
