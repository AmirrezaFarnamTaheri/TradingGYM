"""Recovered convergence assurance module."""
from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, AssuranceReport, evaluate_contract

def evaluate_financial_workspace(evidence, **kwargs):
    """Accepted snapshots aggregate balances, holdings, transaction net, and obligations by currency. Negative: No credential resolution, API call, ledger mutation, payment, or tax filing."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract('read-only financial workspace assurance', data)

class FinancialWorkspaceSnapshot(RecordModel):
    """Recovered target-native contract for account-linked transactions and holdings."""
    pass
