"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import evaluate_contract


def evaluate_agent_architecture(evidence, **kwargs):
    """Every capability has exactly one state owner and the dependency graph is acyclic. Negative: Architecture review cannot deploy, launch, connect, or grant authority."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("agent architecture profile assurance", data)
