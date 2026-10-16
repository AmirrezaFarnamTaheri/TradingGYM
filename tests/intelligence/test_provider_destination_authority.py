"""Provider destination/security rejections are not recoverable model outages.

A DNS-rebinding/SSRF destination denied at call time MUST NOT authorize the
policy adapter to fall back to a cheaper, risk-on model.
"""

from __future__ import annotations

import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.intelligence.llm_backend import httpx_transport
from trading_gym.intelligence.systemone_native_backend import (
    httpx_systemone_transport,
)


@pytest.fixture()
def local_chat() -> ProviderConfig:
    return ProviderConfig(
        provider_id="chat-security",
        kind=ProviderKind.LOCAL,
        display_name="Bound local chat",
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )


@pytest.fixture()
def local_native() -> ProviderConfig:
    return ProviderConfig(
        provider_id="native-security",
        kind=ProviderKind.LOCAL,
        display_name="Bound native scorer",
        model="fast",
        base_url="http://127.0.0.1:11434",
        wire_protocol="systemone_fixed",
    )


def _reject_destination(_url: str, **_options: object) -> str:
    raise ValueError("Base URL resolves to a disallowed network destination")


def _forbid_http(_self, **_kwargs):
    raise AssertionError("unsafe destination must never reach HTTP client")


def test_chat_destination_revalidation_denies_without_network(
    monkeypatch: pytest.MonkeyPatch, local_chat: ProviderConfig
) -> None:
    import httpx
    from trading_gym.common import security

    monkeypatch.setattr(security, "validate_base_url", _reject_destination)
    monkeypatch.setattr(httpx, "Client", _forbid_http)
    transport = httpx_transport(local_chat)
    with pytest.raises(PermissionError, match="provider_destination_denied"):
        transport(
            local_chat.base_url,
            [{"role": "user", "content": "{}"}],
        )


def test_native_destination_revalidation_denies_without_network(
    monkeypatch: pytest.MonkeyPatch, local_native: ProviderConfig
) -> None:
    from trading_gym.intelligence import systemone_native_backend as native

    monkeypatch.setattr(native, "validate_base_url", _reject_destination)
    monkeypatch.setattr(native.httpx, "Client", _forbid_http)
    transport = httpx_systemone_transport(local_native)
    with pytest.raises(PermissionError, match="provider_destination_denied"):
        transport({"state": {"schema": "system1.market.v1"}, "questions": {}})


def test_chat_destination_failure_never_degrades_to_risk_on_rule(
    monkeypatch: pytest.MonkeyPatch, local_chat: ProviderConfig
) -> None:
    import numpy as np

    from trading_gym.common import security
    from trading_gym.intelligence.base import IntelligenceBackend
    from trading_gym.intelligence.llm_backend import LLMDecisionBackend
    from trading_gym.intelligence.policy_adapter import IntelligencePolicyAdapter
    from trading_gym.intelligence.types import (
        ActionKind,
        BackendHealth,
        DecisionSignal,
    )

    class RiskOnFallback(IntelligenceBackend):
        name = "risk-on-fallback"

        def __init__(self):
            self.called = False

        def is_available(self) -> bool:
            return True

        def health(self) -> BackendHealth:
            return BackendHealth(self.name, True, detail="test fallback")

        def decide(self, _context):
            self.called = True
            return DecisionSignal(
                backend=self.name, action_kind=ActionKind.LONG,
                target_leverage=1.0, confidence=0.9, rationale="fallback",
            )

    fallback = RiskOnFallback()
    # The transport captures its destination validator at construction time.
    # Patch *before* creating the backend to reproduce call-time rejection.
    monkeypatch.setattr(security, "validate_base_url", _reject_destination)
    primary = LLMDecisionBackend(local_chat)
    policy = IntelligencePolicyAdapter(
        primary, action_low=-1.0, action_high=1.0,
        fallback_backend=fallback,
        context_builder=lambda _obs: {
            "symbol": "BTCUSDT",
            "position_units": 0.0,
            "equity": 1000.0,
            "margin_level": None,
            "last_close": 100.0,
            "prev_close": 99.0,
            "recent_returns": (0.01, 0.02),
            "risk_limits": {"min_leverage": -1.0, "max_leverage": 1.0},
        },
    )
    with pytest.raises(PermissionError, match="provider_destination_denied"):
        policy.predict(np.array([0.0, 0.0, 0.0]))
    assert fallback.called is False
