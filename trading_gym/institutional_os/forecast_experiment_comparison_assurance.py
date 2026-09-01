"""Recovered convergence assurance module."""
from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, AssuranceReport, evaluate_contract

def evaluate_forecast_experiment_comparison(evidence, **kwargs):
    """Candidates use identical feature sets, temporal windows, holdout evidence, metrics, and complete seed matrices. Negative: No training, automatic selection, promotion, deployment, recommendation, or trading."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract('forecast experiment-comparison assurance', data)

class ForecastExperimentComparisonPolicy(PolicyModel):
    """Recovered target-native contract for window, feature, baseline, coverage, and seed parity guardrails."""
    pass
