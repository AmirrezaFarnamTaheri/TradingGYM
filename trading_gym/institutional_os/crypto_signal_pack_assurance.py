"""Recovered convergence assurance module."""
from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, AssuranceReport, evaluate_contract

def evaluate_crypto_signal_pack(evidence, **kwargs):
    """Signals are point-in-time, hash-bound, independently grouped, source-quorum checked, and aggregated transparently. Negative: No remote fetch, recommendation, alert dispatch, model promotion, or order submission."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract('crypto signal-pack assurance', data)

class CryptoSignalPackPolicy(PolicyModel):
    """Recovered target-native contract for lookahead, duplicate-data, freshness, primary-news, and concentration guardrails."""
    pass
