"""Recovered convergence assurance module."""
from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, AssuranceReport, evaluate_contract

def evaluate_multi_stage_contest(evidence, **kwargs):
    """Every later-stage candidate must have a valid previous-stage advancement and complete independent evaluation matrix. Negative: No candidate execution, model promotion, order submission, or portfolio mutation occurs."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract('multi-stage contest assurance', data)
