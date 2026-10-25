"""Native System-One backend and provider-routing safety tests."""

import json

import httpx
import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.core.intelligence_config import IntelligenceConfig
from trading_gym.agent.intelligence.factory import build_intelligence_policy
from trading_gym.agent.intelligence.llm_backend import LLMDecisionBackend
from trading_gym.agent.intelligence.systemone_native_backend import (
    NativeSystemOneDecisionBackend,
    httpx_systemone_transport,
)
from trading_gym.agent.intelligence.types import (
    ActionKind,
    BackendUnavailable,
    InvalidDecisionSignal,
    MarketContext,
)


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
                    "hold": 0.05,
                    "long": 0.90,
                    "short": 0.05,
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


def test_malformed_native_distribution_is_terminal_authority_refusal():
    wrong = _answer()
    wrong["answers"]["direction"]["probabilities"]["long"] = float("nan")
    backend = NativeSystemOneDecisionBackend(_provider(), transport=lambda body: wrong)
    with pytest.raises(PermissionError, match="systemone_invalid_model_output"):
        backend.decide(_context())


def test_weak_direction_allows_explicit_fallback_without_fabricating_a_trade():
    weak = _answer()
    weak["answers"]["direction"]["confidence"] = 0.2
    backend = NativeSystemOneDecisionBackend(_provider(), transport=lambda body: weak)
    with pytest.raises(BackendUnavailable, match="systemone_low_confidence"):
        backend.decide(_context())


def test_native_transport_uses_bounded_budget_and_disables_proxy_redirects(
    monkeypatch,
    tmp_path,
    mock_httpx_client,
):
    import trading_gym.agent.intelligence.systemone_native_backend as module

    seen = []

    def handle(request):
        seen.append((str(request.url), json.loads(request.content)))
        assert request.method == "POST"
        assert request.headers["Accept-Encoding"] == "identity"
        assert all(0 < value <= 5.0 for value in request.extensions["timeout"].values())
        return httpx.Response(
            200, stream=httpx.ByteStream(json.dumps(_answer()).encode())
        )

    monkeypatch.setattr(
        module, "default_request_budget_path", lambda *args: tmp_path / "budget.json"
    )
    monkeypatch.setattr(module, "validate_base_url", lambda url, **kw: url)
    options = mock_httpx_client(handle)
    transport = httpx_systemone_transport(_provider(max_requests_per_hour=1))
    assert transport({"model": "gemma-fast", "state": {}, "questions": {}})
    assert seen[0][0] == "http://127.0.0.1:8080/v1/systemone"
    with pytest.raises(PermissionError, match="provider_request_budget_denied"):
        transport({"model": "gemma-fast", "state": {}, "questions": {}})
    assert len(seen) == 1
    assert seen[0][1] == {"model": "gemma-fast", "state": {}, "questions": {}}
    assert len(options) == 1 and 0 < options[0]["timeout"] <= 5.0


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


def test_post_only_native_endpoint_reports_reachability_on_http_405(monkeypatch):
    import trading_gym.agent.providers as providers

    urls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers):
            urls.append(url)
            return httpx.Response(
                405, headers={"Allow": "POST"}, request=httpx.Request("GET", url)
            )

    monkeypatch.setattr(providers.httpx, "Client", Client)
    monkeypatch.setattr(providers, "validate_base_url", lambda value, **kwargs: value)
    config = _provider(health_path="/models")
    report = providers.provider_status(config)
    assert report["status"] == "reachable"
    assert report["http_status"] == 405
    assert urls == ["http://127.0.0.1:8080/v1/systemone"]


def test_public_intelligence_exports_include_native_adapter_and_errors():
    import trading_gym.agent.intelligence as api

    assert api.NativeSystemOneDecisionBackend is NativeSystemOneDecisionBackend
    assert api.httpx_systemone_transport is httpx_systemone_transport
    assert issubclass(api.BackendUnavailable, RuntimeError)


@pytest.mark.parametrize("wrong_model", [None, "other-checkpoint", 42])
def test_response_must_be_from_configured_model_not_unqualified_fallback(
    wrong_model,
):
    payload = _answer()
    if wrong_model is None:
        payload.pop("model")
    else:
        payload["model"] = wrong_model
    backend = NativeSystemOneDecisionBackend(
        _provider(),
        transport=lambda _body: payload,
    )
    with pytest.raises(PermissionError, match="model_identity_mismatch"):
        backend.decide(_context())


def test_invalid_max_leverage_rejected_before_native_model_request():
    sent = []
    backend = NativeSystemOneDecisionBackend(
        _provider(),
        transport=lambda body: sent.append(body),
    )
    with pytest.raises(PermissionError, match="invalid_risk_limit"):
        backend.decide(_context(risk_limits={"max_leverage": -2.0}))
    assert not sent


def test_native_global_busy_rate_limits_cannot_be_bypassed_by_key_rotation():
    with pytest.raises(ValueError, match="rate"):
        _provider(rotate_on_rate_limit=True)


@pytest.mark.parametrize(
    "allowed,expected",
    [
        ({}, "unavailable"),
        ({"Allow": "GET"}, "unavailable"),
        ({"Allow": "POST"}, "reachable"),
        ({"Allow": "post, options"}, "reachable"),
    ],
)
def test_method_not_allowed_only_proves_native_route_when_post_advertised(
    monkeypatch,
    allowed,
    expected,
):
    import trading_gym.agent.providers as providers

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers):
            return httpx.Response(
                405, headers=allowed, request=httpx.Request("GET", url)
            )

    monkeypatch.setattr(providers.httpx, "Client", Client)
    monkeypatch.setattr(
        providers,
        "validate_base_url",
        lambda value, **kwargs: value,
    )
    report = providers.provider_status(_provider(health_path="/models"))
    assert report["status"] == expected
    assert report["http_status"] == 405


@pytest.mark.parametrize(
    "raw_response,error_type,denial",
    [
        (b"{not-json", PermissionError, "systemone_malformed_http_response"),
        (b"[]", PermissionError, "systemone_invalid_response_envelope"),
        (
            b"0" * (256 * 1024 + 1),
            InvalidDecisionSignal,
            "provider_response_byte_budget_exceeded",
        ),
    ],
    ids=["malformed-json", "nonobject-json", "oversized-response"],
)
def test_invalid_native_http_payload_is_not_a_recoverable_model_outage(
    monkeypatch,
    mock_httpx_client,
    raw_response,
    error_type,
    denial,
):
    import trading_gym.agent.intelligence.systemone_native_backend as module

    monkeypatch.setattr(module, "validate_base_url", lambda url, **kw: url)
    mock_httpx_client(
        lambda request: httpx.Response(200, stream=httpx.ByteStream(raw_response))
    )
    transport = httpx_systemone_transport(_provider())
    with pytest.raises(error_type, match=denial):
        transport({"model": "gemma-fast", "state": {}, "questions": {}})


def test_fixed_systemone_engine_uses_its_configured_model_without_selector():
    submitted = []
    backend = NativeSystemOneDecisionBackend(
        _provider(wire_protocol="systemone_fixed"),
        transport=lambda body: (submitted.append(body), _answer())[1],
    )
    signal = backend.decide(_context())
    assert signal.action_kind is ActionKind.LONG
    assert "model" not in submitted[0]
    assert submitted[0]["state"]["schema"] == "system1.market.v1"


def test_fixed_native_protocol_cannot_be_misrouted_to_chat_completion():
    provider = _provider(wire_protocol="systemone_fixed")
    with pytest.raises(ValueError, match="native System-One"):
        LLMDecisionBackend(provider)


def test_native_endpoint_join_handles_versioned_or_unversioned_base():
    from trading_gym.agent.providers import join_provider_endpoint

    assert (
        join_provider_endpoint("https://api.example.test", "/v1/systemone")
        == "https://api.example.test/v1/systemone"
    )
    assert (
        join_provider_endpoint("https://api.example.test/v1", "/v1/systemone")
        == "https://api.example.test/v1/systemone"
    )
    assert (
        join_provider_endpoint("https://api.example.test/proxy/v1", "/v1/systemone")
        == "https://api.example.test/proxy/v1/systemone"
    )
    assert (
        join_provider_endpoint("http://127.0.0.1:8080", "/v1/systemone")
        == "http://127.0.0.1:8080/v1/systemone"
    )


def test_native_endpoint_join_does_not_drop_unrelated_base_path():
    from trading_gym.agent.providers import join_provider_endpoint

    assert (
        join_provider_endpoint("https://api.example.test/api", "/v1/systemone")
        == "https://api.example.test/api/v1/systemone"
    )


def test_hosted_systemone_retries_reuse_one_idempotency_key(
    monkeypatch, tmp_path, mock_httpx_client
):
    """Billed hosted requests must not become two logical decisions on retry."""
    import trading_gym.agent.intelligence.systemone_native_backend as module

    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("HOSTED_ONE_KEY", "first-credential")
    monkeypatch.setenv("HOSTED_TWO_KEY", "second-credential")
    monkeypatch.setattr(module, "validate_base_url", lambda url, **kw: url)
    observed = []

    def handle(request):
        observed.append((str(request.url), httpx.Headers(request.headers)))
        code = 401 if len(observed) == 1 else 200
        return httpx.Response(
            code, stream=httpx.ByteStream(json.dumps(_answer()).encode())
        )

    mock_httpx_client(handle)
    config = ProviderConfig(
        provider_id="hosted-system-one",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        display_name="Hosted typed scorer",
        model="gemma-fast",
        base_url="https://system-one.dev/v1",
        wire_protocol="systemone",
        api_key_envs=("HOSTED_ONE_KEY", "HOSTED_TWO_KEY"),
    )
    call = httpx_systemone_transport(config)
    result = call({"model": "gemma-fast", "state": {}, "questions": {}})
    assert result["model"] == "gemma-fast"
    assert len(observed) == 2
    assert all(
        url == "https://system-one.dev/v1/systemone" for url, _headers in observed
    )
    first_key = observed[0][1]["Idempotency-Key"]
    second_key = observed[1][1]["Idempotency-Key"]
    assert first_key == second_key
    assert len(first_key) >= 24
    assert observed[0][1]["Authorization"] == "Bearer first-credential"
    assert observed[1][1]["Authorization"] == "Bearer second-credential"

    call({"model": "gemma-fast", "state": {}, "questions": {}})
    assert len(observed) == 3
    assert observed[2][1]["Idempotency-Key"] != first_key


def test_fixed_engine_uses_no_hosted_idempotency_header(monkeypatch, mock_httpx_client):
    import trading_gym.agent.intelligence.systemone_native_backend as module

    monkeypatch.setattr(module, "validate_base_url", lambda url, **kw: url)
    headers_seen = []

    def handle(request):
        headers_seen.append(httpx.Headers(request.headers))
        return httpx.Response(
            200, stream=httpx.ByteStream(json.dumps(_answer()).encode())
        )

    mock_httpx_client(handle)
    call = httpx_systemone_transport(_provider(wire_protocol="systemone_fixed"))
    call({"state": {}, "questions": {}})
    assert len(headers_seen) == 1
    assert "Idempotency-Key" not in headers_seen[0]
