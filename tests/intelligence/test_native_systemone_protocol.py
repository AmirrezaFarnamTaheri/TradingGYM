"""Native choice + risk-off System-One protocol regression cases."""

import json

import pytest

from trading_gym.intelligence.systemone_protocol import build_request, parse_decision


def _result(*, selected="long", probabilities=None, confidence=0.9, risk_off=0.1):
    if probabilities is None:
        probabilities = {"hold": 0.05, "long": 0.90, "short": 0.05}
    return {
        "model": "gemma-fast",
        "answers": {
            "direction": {
                "type": "choice",
                "choice": selected,
                "probabilities": probabilities,
                "confidence": confidence,
            },
            "risk_off": {"type": "noul", "noul": risk_off},
        },
        "usage": {"input_tokens": 42, "output_tokens": 12},
    }


def test_structured_market_state_is_not_transformed_to_generated_prompt():
    packet = {
        "schema": "system1.market.v1",
        "authority": "advisory_only; never executes orders",
        "market": {"symbol": "BTCUSDT", "last_close": 99},
        "risk_constraints": {"max_leverage": 1},
    }
    body = build_request(json.dumps(packet), "gemma-fast")
    assert body["state"] == packet
    assert body["model"] == "gemma-fast"
    assert body["questions"]["direction"]["type"] == "choice"
    assert set(body["questions"]["direction"]["criteria"]) == {
        "hold", "long", "short",
    }
    assert body["questions"]["risk_off"]["type"] == "noul"


def test_valid_direction_is_bounded_by_operator_and_context_caps():
    label, leverage, confidence, explanation = parse_decision(
        _result(), confidence_floor=0.65, max_leverage=2, risk_ceiling=0.5
    )
    assert label == "long"
    assert leverage == pytest.approx(0.5)
    assert confidence == pytest.approx(0.9)
    assert "uncalibrated" in explanation
    assert parse_decision(
        _result(
            selected="short",
            probabilities={"hold": 0.05, "long": 0.05, "short": 0.9},
        ),
        confidence_floor=0.65, max_leverage=1,
    )[1] == pytest.approx(-1.0)


def test_risk_off_tied_winners_and_zero_limit_force_hold():
    assert parse_decision(
        _result(risk_off=0.7), confidence_floor=0.65, max_leverage=2
    )[:2] == ("hold", 0.0)
    tied = _result(
        selected="long",
        probabilities={"hold": 0.1, "long": 0.45, "short": 0.45},
        confidence=0.4,
    )
    assert parse_decision(
        tied, confidence_floor=0.0, max_leverage=2
    )[:2] == ("hold", 0.0)
    assert parse_decision(
        _result(), confidence_floor=0.65, max_leverage=2, risk_ceiling=0
    )[:2] == ("hold", 0.0)


@pytest.mark.parametrize(
    "result",
    [
        _result(confidence=0.2),
        _result(
            probabilities={"hold": 0.2, "long": 0.6, "short": 0.2},
            confidence=0.9,
        ),
    ],
)
def test_weak_native_advice_cannot_infer_a_confident_trade(result):
    with pytest.raises(ValueError, match="low_confidence"):
        parse_decision(result, confidence_floor=0.65, max_leverage=1)


@pytest.mark.parametrize(
    "distribution",
    [
        {"hold": 0.0, "long": 0.9, "short": 0.9},
        {"hold": 0.0, "long": float("nan"), "short": 1},
        {"hold": 0.1, "long": True, "short": 0.9},
        {"hold": 0.1, "long": 0.9},
        {"hold": -0.1, "long": 0.9, "short": 0.2},
    ],
)
def test_malformed_distribution_denied(distribution):
    with pytest.raises(ValueError, match="systemone_"):
        parse_decision(
            _result(probabilities=distribution),
            confidence_floor=0,
            max_leverage=1,
        )


def test_choice_must_match_winning_probability():
    with pytest.raises(ValueError, match="choice"):
        parse_decision(
            _result(selected="short"),
            confidence_floor=0.5, max_leverage=1,
        )


@pytest.mark.parametrize("risk", [float("nan"), None, False, 1.1])
def test_malformed_noul_probability_denied(risk):
    with pytest.raises(ValueError, match="systemone_"):
        parse_decision(
            _result(risk_off=risk),
            confidence_floor=0.5, max_leverage=1,
        )


@pytest.mark.parametrize(
    "floor,cap,ceiling",
    [(True, 1, None), (0.5, 0, None), (0.5, float("nan"), None),
     (0.5, 1, -1), (0.5, 1, float("inf"))],
)
def test_nonfinite_or_invalid_limits_rejected(floor, cap, ceiling):
    with pytest.raises(ValueError):
        parse_decision(
            _result(),
            confidence_floor=floor,
            max_leverage=cap,
            risk_ceiling=ceiling,
        )


def test_extra_decision_fields_cannot_inject_trade_instructions():
    result = _result()
    result["answers"]["direction"]["place_order_now"] = True
    with pytest.raises(ValueError, match="systemone_"):
        parse_decision(result, confidence_floor=0.5, max_leverage=1)


def test_explicit_low_confidence_hold_does_not_escalate_to_risk_on_fallback():
    payload = _result(
        selected="hold",
        probabilities={"hold": 0.51, "long": 0.30, "short": 0.19},
        confidence=0.40,
    )
    selected, leverage, _, explanation = parse_decision(
        payload, confidence_floor=0.65, max_leverage=1,
    )
    assert selected == "hold"
    assert leverage == 0
    assert "hold" in explanation


def test_fixed_engine_omits_model_selector_but_preserves_typed_state():
    packet = {
        "schema": "system1.market.v1",
        "authority": "advisory_only; never executes orders",
        "market": {"symbol": "BTCUSDT", "last_close": 110.0},
    }
    body = build_request(
        json.dumps(packet), "mlx-community/Qwen3-1.7B-4bit",
        include_model=False,
    )
    assert "model" not in body
    assert body["state"] == packet
    assert tuple(body["questions"]) == ("direction", "risk_off")


def test_invalid_native_model_selector_policy_is_rejected():
    packet = {"schema": "system1.market.v1",
              "authority": "advisory_only; never executes orders"}
    with pytest.raises(ValueError):
        build_request(json.dumps(packet), "engine-1", include_model="false")
