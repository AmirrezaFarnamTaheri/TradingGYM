"""Bounded multi-perspective research prompts; no persona may execute trades."""

from __future__ import annotations

from trading_gym.research.models import ResearchDossier


def debate_prompt_pack(dossier: ResearchDossier) -> dict[str, str]:
    evidence_ids = ", ".join(item.evidence_id for item in dossier.evidence) or "none"
    common = (
        f"Subject: {dossier.subject}. As-of: {dossier.as_of.isoformat()}. "
        f"Use only dossier evidence IDs: {evidence_ids}. Cite every factual claim in [evidence_id] form. "
        "Explicitly identify uncertainty. Do not give personal financial advice and do not request or execute a trade."
    )
    return {
        "bull": common
        + " Build the strongest evidence-supported upside case and list falsifiers.",
        "bear": common
        + " Build the strongest evidence-supported downside case and list falsifiers.",
        "moderator": common
        + " Reconcile agreements and disagreements, score evidence quality, expose unsupported claims, and present conditional scenarios. Do not choose a winner.",
    }
