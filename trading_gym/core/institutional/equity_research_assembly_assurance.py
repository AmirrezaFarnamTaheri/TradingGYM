"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import evaluate_contract


def evaluate_equity_research_assembly(evidence, **kwargs):
    """Sources, assignments, artifacts, sections, charts, reviews, sets, and final report hashes reproduce with complete coverage and role separation. Negative: No fetch, model execution, publication, advice, portfolio mutation, or order submission occurs."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("equity-research assembly assurance", data)
