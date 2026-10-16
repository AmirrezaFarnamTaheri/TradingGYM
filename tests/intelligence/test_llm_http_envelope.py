"""Successful HTTP transport corruption must never authorize a fallback trade."""

from __future__ import annotations

import json

import httpx
import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.intelligence.llm_backend import (
    LLMTransportResponse,
    httpx_transport,
)
from trading_gym.intelligence.system1_backend import System1DecisionBackend
from trading_gym.intelligence.types import MarketContext


def _config() -> ProviderConfig:
    return ProviderConfig(
        provider_id="local-http",
        display_name="test-model",
        kind=ProviderKind.LOCAL,
        model="fast",
        base_url="http://127.0.0.1:11434/v1",
    )


@pytest.mark.parametrize(
    "payload,expected_error",
    [
        (b"{broken", "llm_malformed_http_response"),
        (b"[]", "llm_invalid_response_envelope"),
        (b"{}", "llm_invalid_response_envelope"),
        (
            b'{"choices":[{"message":{"content": 99}}]}',
            "llm_invalid_response_envelope",
        ),
        (b" " * (256 * 1024 + 1), "llm_response_byte_budget_exceeded"),
    ],
)
def test_successful_http_with_invalid_evidence_is_terminal_not_provider_outage(
    monkeypatch: pytest.MonkeyPatch, payload: bytes, expected_error: str
) -> None:
    from trading_gym.common import security

    seen = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, *, headers, json):
            seen.append(url)
            return httpx.Response(
                200, content=payload, request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr(
        security, "validate_base_url", lambda value, **kwargs: value
    )
    request = [
        {"role": "system", "content": "classify"},
        {"role": "user", "content": "{}"},
    ]
    with pytest.raises(PermissionError, match=expected_error):
        httpx_transport(_config())(_config().base_url, request)
    assert len(seen) == 1


def test_valid_success_envelope_keeps_normal_transport_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trading_gym.common import security

    body = json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, *, headers, json):
            return httpx.Response(
                200, content=body, request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr(
        security, "validate_base_url", lambda value, **kwargs: value
    )
    result = httpx_transport(_config())(
        _config().base_url, [{"role": "user", "content": "{}"}]
    )
    assert result.content == "hello"


def test_oversized_strict_system1_text_is_terminal_evidence_violation():
    config = _config()
    backend = System1DecisionBackend(
        config,
        transport=lambda _url, _messages: LLMTransportResponse(
            content="A" * 70_000
        ),
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
