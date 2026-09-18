"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import evaluate_contract


def evaluate_journal_quality(evidence, **kwargs):
    """Quality deductions are explicit and reproducible from the journal report. Negative: A quality score never grants trading authority."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("journal quality evidence", data)
