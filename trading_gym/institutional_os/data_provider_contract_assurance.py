"""Recovered convergence assurance module."""

from __future__ import annotations
from .assurance_contract import RecordModel, PolicyModel, evaluate_contract


def evaluate_provider_contracts(evidence, **kwargs):
    """Provider calls and observations are hash-bound, point-in-time, independently sourced, and reconciled read-only. Negative: No credential resolution, network call, account read, mutation, or execution authority."""
    data = evidence.model_dump() if hasattr(evidence, "model_dump") else dict(evidence)
    data.update(kwargs)
    return evaluate_contract("provider contract assurance", data)


class ProviderRouteProfile(RecordModel):
    """Recovered target-native contract for canonical routes, aliases, and deprecated-route migration."""

    pass


class ProviderCallIntent(RecordModel):
    """Recovered target-native contract for route parameters, pagination, and provider request limits."""

    pass


class ReconciledProviderField(RecordModel):
    """Recovered target-native contract for provider-group diversity and conflicting-value handling."""

    pass


class ProviderContractPolicy(PolicyModel):
    """Recovered target-native contract for account and provider mutation surfaces."""

    pass
