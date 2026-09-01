"""Recovered convergence assurance module."""
from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, AssuranceReport, evaluate_contract

def evaluate_investment_research_pipeline(evidence, **kwargs):
    """Research sources, claims, stage artifacts, artifact sets, and reviews are hash-bound and complete. Negative: Qualification cannot issue advice, promote a model, submit an order, or grant execution authority."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract('investment research pipeline', data)

class ResearchPipelineReview(RecordModel):
    """Recovered target-native contract for peer, risk, catalyst, counterthesis, and independent review requirements."""
    pass

class InvestmentResearchSource(RecordModel):
    """Recovered target-native contract for factor, backtest, ML, and point-in-time research methodology."""
    pass
