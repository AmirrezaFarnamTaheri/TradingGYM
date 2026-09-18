"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import evaluate_contract


def evaluate_financial_ui_projection(evidence, **kwargs):
    """Events are contiguous, parented, bounded, redacted, and grounded in declared tool results and sources. Negative: Projection state cannot execute tools, mutate UI authority, load external assets, disclose secrets, pay, or trade."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("financial UI projection assurance", data)
