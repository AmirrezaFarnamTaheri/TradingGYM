"""Deterministic dossier assembly with explicit missing-data semantics."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from trading_gym.research.bootstrap_ci import bca_interval, sharpe_ratio
from trading_gym.research.models import EvidenceItem, ResearchDossier, ResearchSection

SECTION_ORDER = ("market", "fundamentals", "macro", "news", "risk", "technical")

#: Statistics a dossier can bootstrap from ``metadata["returns"]`` payloads.
#: Registration keeps the contract explicit instead of executing arbitrary
#: callables smuggled through evidence metadata.
RETURNS_STATISTICS: dict[str, Callable[[np.ndarray], float]] = {
    "mean": lambda arr: float(arr.mean()),
    "sharpe": lambda arr: sharpe_ratio(arr),
}

DEFAULT_RETURNS_STATISTIC = "sharpe"


def _coerce_returns(payload: Any, evidence_id: str) -> np.ndarray:
    """Validate a ``metadata["returns"]`` payload; fail loudly, never guess."""
    if isinstance(payload, dict) or isinstance(payload, (str, bytes)):
        raise ValueError(
            f"evidence {evidence_id}: metadata['returns'] must be a numeric sequence"
        )
    try:
        values = [float(v) for v in payload]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"evidence {evidence_id}: metadata['returns'] must be a numeric sequence"
        ) from exc
    if len(values) < 2:
        raise ValueError(
            f"evidence {evidence_id}: BCa intervals need at least two returns"
        )
    if any(not math.isfinite(value) for value in values):
        raise ValueError(f"evidence {evidence_id}: metadata['returns'] must be finite")
    return np.asarray(values, dtype=float)


def _bca_facts_for_item(
    item: EvidenceItem,
    *,
    confidence: float,
    n_resamples: int,
) -> tuple[list[str], list[str]]:
    """BCa confidence-interval facts for one evidence item's return series."""
    findings: list[str] = []
    limitations: list[str] = []
    if "returns" not in item.metadata:
        return findings, limitations
    returns = _coerce_returns(item.metadata["returns"], item.evidence_id)
    statistic_name = (
        str(item.metadata.get("returns_statistic", DEFAULT_RETURNS_STATISTIC))
        .strip()
        .lower()
    )
    statistic = RETURNS_STATISTICS.get(statistic_name)
    if statistic is None:
        raise ValueError(
            f"evidence {item.evidence_id}: unknown returns_statistic "
            f"{statistic_name!r}; available: {sorted(RETURNS_STATISTICS)}"
        )
    interval = bca_interval(
        returns,
        statistic,
        confidence=confidence,
        n_resamples=n_resamples,
    )
    findings.append(
        f"[{item.evidence_id}] BCa {confidence:.0%} confidence interval for "
        f"{statistic_name}: [{interval.lower:.6g}, {interval.upper:.6g}] "
        f"(estimate {interval.estimate:.6g}, n={returns.size}, "
        f"resamples={interval.n_resamples})"
    )
    if interval.degenerate:
        limitations.append(
            f"[{item.evidence_id}] Bootstrap distribution was constant; the "
            f"reported {statistic_name} interval is a point estimate, not "
            "evidence of precision."
        )
    return findings, limitations


def build_dossier(
    *,
    subject: str,
    evidence: list[EvidenceItem],
    required_sections: tuple[str, ...] = SECTION_ORDER,
    max_items: int = 500,
    bca_confidence: float | None = 0.95,
    bca_n_resamples: int = 999,
) -> ResearchDossier:
    if len(evidence) > max_items:
        raise ValueError(f"evidence count exceeds {max_items} item limit")
    ids = [item.evidence_id for item in evidence]
    if len(ids) != len(set(ids)):
        raise ValueError("evidence IDs must be unique")
    grouped: dict[str, list[EvidenceItem]] = defaultdict(list)
    for item in evidence:
        category = str(item.metadata.get("category", "uncategorised")).lower()
        grouped[category].append(item)

    if bca_confidence is not None and not 0.0 < bca_confidence < 1.0:
        raise ValueError("bca_confidence must be within (0, 1) or None to disable")

    sections: list[ResearchSection] = []
    missing: list[str] = []
    for section_name in required_sections:
        items = grouped.get(section_name, [])
        if not items:
            missing.append(section_name)
            sections.append(
                ResearchSection(
                    name=section_name,
                    status="missing",
                    limitations=[
                        "No evidence was supplied for this section; absence must not be interpreted as a negative finding."
                    ],
                )
            )
            continue
        facts: list[str] = []
        section_limitations: list[str] = []
        for item in sorted(
            items,
            key=lambda entry: (
                entry.published_at or entry.observed_at,
                entry.evidence_id,
            ),
            reverse=True,
        ):
            for fact in item.facts:
                facts.append(f"[{item.evidence_id}] {fact}")
            if bca_confidence is not None:
                extra_facts, extra_limitations = _bca_facts_for_item(
                    item,
                    confidence=bca_confidence,
                    n_resamples=bca_n_resamples,
                )
                facts.extend(extra_facts)
                section_limitations.extend(extra_limitations)
        sections.append(
            ResearchSection(
                name=section_name,
                status="available",
                evidence_ids=[item.evidence_id for item in items],
                findings=facts[:200],
                limitations=section_limitations[:50],
            )
        )

    uncategorised = grouped.get("uncategorised", [])
    if uncategorised:
        sections.append(
            ResearchSection(
                name="uncategorised",
                status="available",
                evidence_ids=[item.evidence_id for item in uncategorised],
                findings=[
                    f"[{item.evidence_id}] {fact}"
                    for item in uncategorised
                    for fact in item.facts
                ][:200],
                limitations=[
                    "Evidence provider did not declare a recognised category."
                ],
            )
        )

    caveats = [
        "Evidence timestamps, licensing, revisions, and provider outages may affect completeness."
    ]
    if bca_confidence is not None and any(
        "returns" in item.metadata for item in evidence
    ):
        caveats.append(
            "BCa confidence intervals assume i.i.d. resampling of the supplied "
            "return series; autocorrelated series should additionally be read "
            "against block-bootstrap intervals."
        )

    return ResearchDossier(
        subject=subject,
        as_of=datetime.now(timezone.utc),
        sections=sections,
        evidence=evidence,
        missing_data=missing,
        caveats=caveats,
    )
