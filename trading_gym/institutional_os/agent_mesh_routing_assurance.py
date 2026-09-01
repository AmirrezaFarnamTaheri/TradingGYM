"""Recovered convergence assurance module."""
from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, AssuranceReport, evaluate_contract

def evaluate_agent_mesh_routing(evidence, **kwargs):
    """Routes and envelopes are tenant-bound, hash-bound, ordered, bounded, and lineage-checked. Negative: No broker publication, network delivery, route mutation, or agent invocation."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract('agent-mesh routing assurance', data)

class AgentMeshRoutingPolicy(PolicyModel):
    """Recovered target-native contract for sequence, hop, parent, causation, and fanout failure semantics."""
    pass
