"""Recovered convergence assurance module."""
from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, AssuranceReport, evaluate_contract

def evaluate_embedded_widget(evidence, **kwargs):
    """Widget manifests bind relative assets, callbacks, hashes, capabilities, CSP, sandbox tokens, size limits, and expiry. Negative: Assessment never mounts a widget or executes a script."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract('embedded-widget assurance', data)

class EmbeddedWidgetPolicy(PolicyModel):
    """Recovered target-native contract for wallet, payment, order-ticket, external-script, inline/eval, and mutating callback guardrails."""
    pass
