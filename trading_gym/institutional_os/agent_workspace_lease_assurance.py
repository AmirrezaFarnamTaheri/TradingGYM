"""Recovered convergence assurance module."""
from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, AssuranceReport, evaluate_contract

def evaluate_workspace_leases(evidence, **kwargs):
    """Active claims are bound to repository snapshot, agent, scope, epoch, version, and expiry. Negative: No worktree, file, Git mutation, or lock authority is granted."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract('fenced workspace lease assurance', data)
