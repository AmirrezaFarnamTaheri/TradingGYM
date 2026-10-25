"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import PolicyModel, evaluate_contract


def evaluate_harness_benchmark(evidence, **kwargs):
    """Every result binds to a declared case, candidate, schema, assertion set, budget, and content hash. Negative: Assessment never invokes models, agents, tools, or networks."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("harness benchmark assurance", data)


class HarnessBenchmarkPolicy(PolicyModel):
    """Recovered target-native contract for benchmark budget, cardinality, schema, and pass-ratio guardrails."""

    pass
