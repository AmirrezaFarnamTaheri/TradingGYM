import json
import httpx
import pytest
from trading_gym.agent.strategy_planner import AgentDirective, LLMStrategyPlanner


def test_directive_cannot_raise_exposure_or_introduce_unknown_arms():
    with pytest.raises(ValueError):
        AgentDirective(
            regime="chop",
            arm_weights={"rules": 1},
            exposure_multiplier=2,
            rationale="bad",
        )
    with pytest.raises(ValueError):
        AgentDirective(
            regime="chop",
            arm_weights={"unknown": 1},
            exposure_multiplier=0.5,
            rationale="bad",
        ).bounded_weights()
    with pytest.raises(ValueError):
        AgentDirective(
            regime="chop",
            arm_weights={"rules": float("nan")},
            exposure_multiplier=0.5,
            rationale="bad",
        ).bounded_weights()


def test_slow_planner_reads_structured_output_without_order_authority():
    observed = []

    def respond(request):
        observed.append(json.loads(request.content))
        body = {
            "regime": "chop",
            "arm_weights": {"rules": 0.4, "stats": 1.0},
            "exposure_multiplier": 0.5,
            "halted_symbols": [],
            "rationale": "Low directional agreement.",
        }
        return httpx.Response(
            200,
            stream=httpx.ByteStream(
                json.dumps(
                    {"choices": [{"message": {"content": json.dumps(body)}}]}
                ).encode()
            ),
        )

    planner = LLMStrategyPlanner(
        "http://127.0.0.1:11434/v1",
        "local-test",
        transport=httpx.MockTransport(respond),
    )
    result = planner.plan({"markets": []})
    assert result.exposure_multiplier == 0.5
    assert result.bounded_weights()["stats"] == 1.0
    assert observed[0]["model"] == "local-test"


def test_provider_failure_does_not_parse_an_order_or_fabricate_a_plan():
    planner = LLMStrategyPlanner(
        "http://127.0.0.1:11434/v1",
        "local-test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                503, stream=httpx.ByteStream(b'{"error":"offline"}')
            )
        ),
    )
    with pytest.raises(RuntimeError, match="request failed"):
        planner.plan({})
