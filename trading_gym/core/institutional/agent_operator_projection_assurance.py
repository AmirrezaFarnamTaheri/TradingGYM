"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import evaluate_contract


def evaluate_operator_projection(evidence, **kwargs):
    """Projection data is tenant-bound, hash-bound, redacted, ordered, fresh, and internally consistent. Negative: The projection cannot launch agents, retry/cancel tasks, mutate state, or load external assets."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("read-only operator projection assurance", data)
