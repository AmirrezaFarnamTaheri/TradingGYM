"""Recovered convergence assurance module."""
from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, AssuranceReport, evaluate_contract

def evaluate_wallet_action(evidence, **kwargs):
    """Wallet actions bind session, policy, nonce, usage, simulation, and approvals. Negative: No key access, signing, broadcast, provider call, or funds movement."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract('wallet action assurance', data)

class WalletActionPolicy(PolicyModel):
    """Recovered target-native contract for signer and transaction execution authority."""
    pass
