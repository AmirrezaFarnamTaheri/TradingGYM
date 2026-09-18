"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import PolicyModel, evaluate_contract


def evaluate_finance_evaluation_campaign(evidence, **kwargs):
    """Cases, retrievals, answers, judge evidence, costs, latency, and metrics are candidate- and set-bound. Negative: No retriever, model, tool, or judge execution and no candidate promotion."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("finance evaluation-campaign assurance", data)


class FinanceEvaluationCampaignPolicy(PolicyModel):
    """Recovered target-native contract for point-in-time and cross-case citation guardrails."""

    pass
