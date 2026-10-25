"""Both chat and native System-1 obey *signed* risk limits."""

from __future__ import annotations

import json

import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.agent.intelligence.llm_backend import LLMTransportResponse
from trading_gym.agent.intelligence.system1_backend import System1DecisionBackend
from trading_gym.agent.intelligence.systemone_native_backend import (
    NativeSystemOneDecisionBackend,
)
from trading_gym.agent.intelligence.types import ActionKind, MarketContext


def context(**limits: float) -> MarketContext:
    return MarketContext(
        symbol="BTCUSDT",
        position_units=0.0,
        equity=1000.0,
        margin_level=None,
        last_close=102.0,
        prev_close=100.0,
        recent_returns=(0.02, -0.01),
        risk_limits=limits,
    )


def chat_backend(action: str, leverage: float, calls: list) -> System1DecisionBackend:
    provider = ProviderConfig(
        provider_id="chat-fast",
        kind=ProviderKind.LOCAL,
        display_name="fast chat",
        model="fast-1",
        base_url="http://127.0.0.1:11434/v1",
    )

    def reply(url, messages):
        calls.append((url, messages))
        return LLMTransportResponse(
            content=json.dumps(
                {
                    "action": action,
                    "target_leverage": leverage,
                    "confidence": 0.95,
                    "rationale": "bounded advisory evidence",
                }
            )
        )

    return System1DecisionBackend(provider, transport=reply)


@pytest.mark.parametrize(
    "action,proposed,expected",
    (("long", 1.0, 0.25), ("short", -1.0, -0.2)),
)
def test_chat_system1_enforces_both_signed_risk_bounds(
    action: str, proposed: float, expected: float
) -> None:
    calls: list = []
    backend = chat_backend(action, proposed, calls)
    result = backend.decide(context(min_leverage=-0.2, max_leverage=0.25))
    assert result.target_leverage == pytest.approx(expected)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "action,proposed,limits",
    (
        ("long", 1.0, {"min_leverage": -1.0, "max_leverage": 0.0}),
        ("short", -1.0, {"min_leverage": 0.0, "max_leverage": 1.0}),
    ),
)
def test_direction_forbidden_by_risk_bounds_becomes_hold(
    action: str, proposed: float, limits: dict[str, float]
) -> None:
    backend = chat_backend(action, proposed, [])
    result = backend.decide(context(**limits))
    assert result.action_kind is ActionKind.HOLD
    assert result.target_leverage == 0.0


@pytest.mark.parametrize(
    "limits",
    (
        {"min_leverage": 0.5, "max_leverage": 1.0},
        {"min_leverage": -1.0, "max_leverage": -0.5},
        {"min_leverage": -1.0, "max_leverage": float("nan")},
    ),
)
def test_bad_risk_bounds_deny_before_provider_request(limits: dict[str, float]) -> None:
    calls: list = []
    backend = chat_backend("long", 0.5, calls)
    with pytest.raises(PermissionError, match="system1_invalid_risk"):
        backend.decide(context(**limits))
    assert calls == []


def test_native_systemone_also_enforces_negative_short_side_bound() -> None:
    provider = ProviderConfig(
        provider_id="native-fast",
        kind=ProviderKind.LOCAL,
        display_name="typed fast",
        model="native-v1",
        wire_protocol="systemone",
        base_url="http://127.0.0.1:8080",
    )
    calls: list = []

    def respond(request):
        calls.append(request)
        return {
            "model": "native-v1",
            "answers": {
                "direction": {
                    "type": "choice",
                    "choice": "short",
                    "probabilities": {"hold": 0.10, "long": 0.05, "short": 0.85},
                    "confidence": 0.9,
                },
                "risk_off": {"type": "noul", "noul": 0.10},
            },
        }

    backend = NativeSystemOneDecisionBackend(provider, transport=respond)
    signal = backend.decide(context(min_leverage=-0.2, max_leverage=1.0))
    assert signal.action_kind is ActionKind.SHORT
    assert signal.target_leverage == pytest.approx(-0.2)
    assert len(calls) == 1


def test_native_systemone_short_only_risk_window_does_not_force_hold() -> None:
    provider = ProviderConfig(
        provider_id="native-short",
        kind=ProviderKind.LOCAL,
        display_name="typed short",
        model="native-short-v1",
        wire_protocol="systemone",
        base_url="http://127.0.0.1:8080",
    )
    backend = NativeSystemOneDecisionBackend(
        provider,
        transport=lambda request: {
            "model": "native-short-v1",
            "answers": {
                "direction": {
                    "type": "choice",
                    "choice": "short",
                    "probabilities": {"hold": 0.1, "long": 0.05, "short": 0.85},
                    "confidence": 0.9,
                },
                "risk_off": {"type": "noul", "noul": 0.1},
            },
        },
    )
    # A zero positive cap prohibits LONG but must not prohibit an allowed
    # -0.25 SHORT simply because the legacy parser used a symmetric cap.
    result = backend.decide(context(min_leverage=-0.25, max_leverage=0.0))
    assert result.action_kind is ActionKind.SHORT
    assert result.target_leverage == pytest.approx(-0.25)
