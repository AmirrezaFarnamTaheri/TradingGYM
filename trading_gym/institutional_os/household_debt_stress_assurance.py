"""Recovered convergence assurance module."""
from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, AssuranceReport, evaluate_contract

def evaluate_household_debt_stress(evidence, **kwargs):
    """Debt, income, expense, reserve, arrears, maturity, and scenario evidence is hash-bound and Decimal-safe. Negative: No advice, lending, refinancing, payment, creditor contact, or account mutation."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract('household debt-stress assurance', data)

class HouseholdDebtStressPolicy(PolicyModel):
    """Recovered target-native contract for arrears and stressed-policy guardrails."""
    pass
