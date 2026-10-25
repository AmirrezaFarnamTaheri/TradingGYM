from __future__ import annotations
from trading_gym.core.security import redact_secrets


def build_status(snapshot):
    return {
        "status": redact_secrets(dict(snapshot)),
        "read_only": True,
        "authority_granted": False,
    }
