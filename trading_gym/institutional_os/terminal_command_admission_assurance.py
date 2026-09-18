"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, evaluate_contract


def evaluate_terminal_command_admission(evidence, **kwargs):
    """Allowed read-only command manifests are hash-bound, confined, and statically qualified. Negative: No process, shell, network, environment load, download, or command execution."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("static terminal command admission", data)


class TerminalCommandCapability(RecordModel):
    """Recovered target-native contract for wallet, transfer, airdrop, transaction, and trading commands."""

    pass


class TerminalCommandAdmissionPolicy(PolicyModel):
    """Recovered target-native contract for terminal pipeline configuration and environment use."""

    pass
