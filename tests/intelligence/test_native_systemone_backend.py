"""Native System-One backend and provider-routing safety tests."""

import httpx
import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.common.intelligence_config import IntelligenceConfig
from trading_gym.intelligence.factory import build_intelligence_policy
from trading_gym.intelligence.llm_backend import LLMDecisionBackend
from trading_gym.intelligence.systemone_native_backend import (
    NativeSystemOneDecisionBackend,
    httpx_systemone_transport,
)
from trading_gym.intelligence.types import ActionKind, BackendUnavailable, MarketContext


def _provider(**updates):
    values = dict(
        provider_id="gemma-typed",
        kind=ProviderKind.LOCAL,
        display_name="Local typed classifier",
        model="gemma-fast",
        base_url="http://127.0.0.1:8080",
        health_path="/health",
        wire_protocol="systemone",
    )
    values.update(updates)
    return ProviderConfig(**values)


def _context(**changes):
    data = dict(
        symbol="BTCUSDT",
        position_units=0.0,
        equity=1000.0,
        margin_level=None,
        last_close=100.0,
        prev_close=99.0,
        recent_returns=(0.01, 0.004),
        drawdown_from_peak=0.02,
    )
    data.update(changes)
    return MarketContext(**data)


def _answer(*, risk_off=0.1):
    return {
        "model": "gemma-fast",
        "answers": {
            "direction": {
                "type": "choice",
                "choice": "long",
                "probabilities": {
                    "hold": 0.05, "long": 0.90, "short": 0.05,
                },
                "confidence": 0.9,
            },
            "risk_off": {"type": "noul", "noul": risk_off},
        },
        "usage": {"input_tokens": 42, "output_tokens": 12},
    }


def test_typed_provider_accepts_safe_paths_and_rejects_protocol_confusion():
    provider = _provider(systemone_path="/v1/systemone")
    assert provider.wire_protocol == "systemone"
    with pytest.raises(ValueError, match="systemone_path"):
        _provider(systemone_path="/v1/../private")
    with pytest.raises(ValueError):
        _provider(wire_protocol="unsupported")
    with pytest.raises(ValueError, match="CLI"):
        ProviderConfig(
            provider_id="cli-typed",
            kind=ProviderKind.CLI,
            display_name="CLI",
            model="local",
            command="binary",
            wire_protocol="systemone",
        )
    with pytest.raises(ValueError, match="native System-One"):
        LLMDecisionBackend(provider)


def test_native_backend_emits_bounded_advice_using_verified_packet():
    observed = []
    backend = NativeSystemOneDecisionBackend(
        _provider(),
        transport=lambda body: (observed.append(body), _answer())[1],
        max_leverage=0.75,
    )
    result = backend.decide(_context(risk_limits={"max_leverage": 0.5}))
    assert result.backend == "system1:gemma-typed"
    assert result.action_kind is ActionKind.LONG
    assert result.target_leverage == pytest.approx(0.5)
    assert "uncalibrated" in result.rationale
    assert observed[0]["state"]["market"]["symbol"] == "BTCUSDT"
    assert len(backend.last_context_sha256) == 64


def test_unverified_live_evidence_denies_before_native_network_or_fallback():
    called = []
    backend = NativeSystemOneDecisionBackend(
        _provider(), transport=lambda body: called.append(body)
    )
    with pytest.raises(PermissionError, match="unverified_live_market"):
        backend.decide(_context(is_live=True))
    assert called == []


def test_risk_off_is_explicit_hold_not_unverified_directional_order():
    backend = NativeSystemOneDecisionBackend(
        _provider(), transport=lambda body: _answer(risk_off=0.9)
    )
    result = backend.decide(_context())
    assert result.action_kind is ActionKind.HOLD
    assert result.target_leverage == 0


def test_invalid_distribution_degrades_without_trading_signal():
    wrong = _answer()
    wrong["answers"]["direction"]["probabilities"]["long"] = float("nan")
    backend = NativeSystemOneDecisionBackend(
        _provider(), transport=lambda body: wrong
    )
    with pytest.raises(BackendUnavailable, match="systemone_"):
        backend.decide(_context())


def test_native_transport_uses_bounded_budget_and_disables_proxy_redirects(
    monkeypatch,
):
    import trading_gym.intelligence.systemone_native_backend as module

    seen = []
    slots = [1]

    class Budget:
        def __init__(self, *args, **kwargs):
            pass

        def reserve(self):
            if not slots[0]:
                from trading_gym.intelligence.llm_request_budget import (
                    RequestBudgetUnavailable,
                )
                raise RequestBudgetUnavailable("exhausted")
            slots[0] -= 1

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, *, headers, json):
            seen.append((url, json))
            return httpx.Response(
                200,
                json=_answer(),
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(module, "ProviderRequestBudget", Budget)
    monkeypatch.setattr(module, "validate_base_url", lambda url, **kw: url)
    monkeypatch.setattr(httpx, "Client", Client)
    transport = httpx_systemone_transport(
        _provider(max_requests_per_hour=1)
    )
    assert transport({"model": "gemma-fast", "state": {}, "questions": {}})
    assert seen[0][0] == "http://127.0.0.1:8080/v1/systemone"
    with pytest.raises(PermissionError, match="provider_request_budget_denied"):
        transport({"model": "gemma-fast", "state": {}, "questions": {}})
    assert len(seen) == 1


def test_system1_factory_selects_native_backend_and_chat_mode_refuses_it():
    provider = _provider()

    class Repository:
        def list(self):
            return [provider]

    policy = build_intelligence_policy(
        config=IntelligenceConfig(
            mode="system1",
            system1_provider_ids=(provider.provider_id,),
        ),
        model=None,
        action_low=-5,
        action_high=5,
        provider_repository=Repository(),
    )
    assert isinstance(policy.backend, NativeSystemOneDecisionBackend)

    with pytest.raises(ValueError, match="native System-One"):
        build_intelligence_policy(
            config=IntelligenceConfig(
                mode="llm",
                llm_provider_id=provider.provider_id,
            ),
            model=None,
            action_low=-5,
            action_high=5,
            provider_repository=Repository(),
        )
