"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import PolicyModel, evaluate_contract


def evaluate_ledger_ingestion_plan(evidence, **kwargs):
    """Every candidate field resolves to encrypted source evidence and a reviewed account match. Negative: No source decryption, credentials, commit, journal posting, or ledger mutation."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("prepare-only ledger ingestion assurance", data)


class LedgerIngestionPlanPolicy(PolicyModel):
    """Recovered target-native contract for duplicate and missing-lineage guardrails."""

    pass
