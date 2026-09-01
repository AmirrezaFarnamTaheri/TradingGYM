"""Recovered convergence assurance module."""
from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, AssuranceReport, evaluate_contract

def evaluate_alternative_market_evidence(evidence, **kwargs):
    """Alternative events require fresh, independent source groups and remain advisory. Negative: No external fetch, notification, recommendation, identity attribution, or order submission."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract('alternative market evidence', data)

class AlternativeEvidenceEvent(RecordModel):
    """Recovered target-native contract for trend/news content fingerprinting and duplicate suppression."""
    pass

class AlternativeEvidenceKind(RecordModel):
    """Recovered target-native contract for political, lobbying, patent, regulatory, and government event taxonomy."""
    pass

class AlternativeEvidencePolicy(PolicyModel):
    """Recovered target-native contract for predictions, sentiment, analyst, and social evidence."""
    pass
