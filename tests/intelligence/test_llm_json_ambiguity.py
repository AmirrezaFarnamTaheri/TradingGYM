"""Reject ambiguous JSON evidence from fast advisory models."""

from __future__ import annotations

import pytest

from trading_gym.agent.providers import ProviderConfig, ProviderKind
from trading_gym.intelligence.llm_backend import (
    LLMTransportResponse,
    _lenient_json_parser,
    _strict_json_parser,
)
from trading_gym.intelligence.system1_backend import System1DecisionBackend
from trading_gym.intelligence.types import MarketContext


@pytest.mark.parametrize(
    "response",
    (
        '{"action":"hold","action":"long","confidence":0.99,'
        '"target_leverage":1.0,"rationale":"contradictory"}',
        '{"action":"long","confidence":0.99,"confidence":0.01,'
        '"target_leverage":1.0,"rationale":"duplicated confidence"}',
        '{"action":"long","confidence":NaN,'
        '"target_leverage":1.0,"rationale":"invalid float"}',
        '{"action":"long","confidence":Infinity,'
        '"target_leverage":1.0,"rationale":"invalid float"}',
    ),
)
def test_strict_json_rejects_duplicate_and_nonstandard_values(response: str) -> None:
    assert _strict_json_parser(response) is None


def test_lenient_json_cannot_silently_choose_last_duplicate_key() -> None:
    response = (
        'Rendered decision: {"action":"hold","action":"short",'
        '"confidence":0.95,"target_leverage":-1,"rationale":"ambiguous"}'
    )
    assert _lenient_json_parser(response) is None


def test_fast_model_duplicate_action_fails_closed_without_trade() -> None:
    provider = ProviderConfig(
        provider_id="local-fast",
        display_name="Test local fast model",
        kind=ProviderKind.LOCAL,
        model="test-fast",
        base_url="http://127.0.0.1:11434/v1",
    )
    raw = (
        '{"action":"hold","action":"long",'
        '"target_leverage":0.75,"confidence":0.99,'
        '"rationale":"contradictory action"}'
    )
    backend = System1DecisionBackend(
        provider,
        transport=lambda _base, _messages: LLMTransportResponse(content=raw),
    )
    context = MarketContext(
        symbol="BTCUSDT",
        position_units=0.0,
        equity=1000.0,
        margin_level=None,
        last_close=105.0,
        prev_close=104.0,
        recent_returns=(0.01, -0.005, 0.003),
        risk_limits={"min_leverage": -1.0, "max_leverage": 1.0},
    )
    with pytest.raises(PermissionError, match="system1_unparseable_model_output"):
        backend.decide(context)


def test_valid_structured_decision_remains_parseable() -> None:
    response = (
        '{"action":"hold","target_leverage":0,"confidence":0.9,'
        '"rationale":"insufficient signal"}'
    )
    assert _strict_json_parser(response)["action"] == "hold"
