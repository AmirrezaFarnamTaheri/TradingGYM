"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import evaluate_contract


def evaluate_finance_skill_chain(evidence, **kwargs):
    """All sources, nodes, artifacts, sets, chronology, dependencies, capabilities, and review outputs reproduce and satisfy policy. Negative: No skill, model, tool, network client, publisher, adviser, payment, credential, or order path is executed or authorised."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("finance-skill chain assurance", data)
