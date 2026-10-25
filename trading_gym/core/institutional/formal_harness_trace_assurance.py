"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import PolicyModel, evaluate_contract


def evaluate_formal_harness_trace(evidence, **kwargs):
    """Genesis, state transitions, previous hashes, claims, checker identity, proof artifacts, and axioms are bound. Negative: No proof assistant, compiler, or harness execution and no target-issued theorem certification."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("formal harness-trace assurance", data)


class FormalHarnessTracePolicy(PolicyModel):
    """Recovered target-native contract for broken chain and open axiom inventory guardrails."""

    pass
