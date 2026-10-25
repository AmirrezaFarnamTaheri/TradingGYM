"""Successful HTTP transport corruption must never authorize a fallback trade."""

from __future__ import annotations

import json

import httpx
import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.agent.intelligence.llm_backend import (
    LLMTransportResponse,
    httpx_transport,
)
from trading_gym.agent.intelligence.system1_backend import System1DecisionBackend
from trading_gym.agent.intelligence.types import InvalidDecisionSignal, MarketContext


def _config() -> ProviderConfig:
    return ProviderConfig(
        provider_id="local-http",
        display_name="test-model",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )


@pytest.mark.parametrize(
    "payload,error_type,expected_error",
    [
        (b"{broken", PermissionError, "llm_malformed_http_response"),
        (b"[]", PermissionError, "llm_invalid_response_envelope"),
        (b"{}", PermissionError, "llm_invalid_response_envelope"),
        (
            b'{"choices":[{"message":{"content": 99}}]}',
            PermissionError,
            "llm_invalid_response_envelope",
        ),
        (
            b" " * (256 * 1024 + 1),
            InvalidDecisionSignal,
            "provider_response_byte_budget_exceeded",
        ),
    ],
    ids=[
        "malformed-json",
        "nonobject-json",
        "missing-choices",
        "nontext-content",
        "oversized-stream",
    ],
)
def test_successful_http_with_invalid_evidence_is_terminal_not_provider_outage(
    monkeypatch: pytest.MonkeyPatch,
    mock_httpx_client,
    payload: bytes,
    error_type,
    expected_error: str,
) -> None:
    from trading_gym.core import security

    seen = []

    def handle(request):
        seen.append(request)
        assert request.method == "POST"
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(200, stream=httpx.ByteStream(payload))

    mock_httpx_client(handle)
    monkeypatch.setattr(security, "validate_base_url", lambda value, **kwargs: value)
    request = [
        {"role": "system", "content": "classify"},
        {"role": "user", "content": "{}"},
    ]
    with pytest.raises(error_type, match=expected_error):
        httpx_transport(_config())(_config().base_url, request)
    assert len(seen) == 1


def test_valid_success_envelope_keeps_normal_transport_behavior(
    monkeypatch: pytest.MonkeyPatch,
    mock_httpx_client,
) -> None:
    from trading_gym.core import security

    body = json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    mock_httpx_client(
        lambda request: httpx.Response(200, stream=httpx.ByteStream(body))
    )
    monkeypatch.setattr(security, "validate_base_url", lambda value, **kwargs: value)
    result = httpx_transport(_config())(
        _config().base_url, [{"role": "user", "content": "{}"}]
    )
    assert result.content == "hello"


def test_oversized_strict_system1_text_is_terminal_evidence_violation():
    config = _config()
    backend = System1DecisionBackend(
        config,
        transport=lambda _url, _messages: LLMTransportResponse(content="A" * 70_000),
    )
    with pytest.raises(PermissionError, match="system1_response_text_budget"):
        backend.decide(
            MarketContext(
                symbol="BTCUSDT",
                position_units=0,
                equity=1000,
                margin_level=None,
                last_close=100,
                prev_close=99,
                recent_returns=(0.01, 0.02),
            )
        )
