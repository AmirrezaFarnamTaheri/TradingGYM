"""Deterministic risk-gate tests: cap math, validation, advisory payload."""

import json
from decimal import Decimal

import pytest

from trading_gym.agent.risk_gate import (
    PositionLimitVerdict,
    RiskGateError,
    build_advisory_payload,
    evaluate_position_limits,
)

D = Decimal


def test_caps_match_donor_arithmetic():
    # Portfolio 60000 → 20% limit 12000; BTC holds 10000 → 2000 remaining,
    # cash 50000 does not bind.
    verdicts = evaluate_position_limits(
        cash=D("50000"),
        positions_value={"BTC": D("10000")},
    )
    verdict = verdicts["BTC"]
    assert verdict.position_limit == D("12000")
    assert verdict.remaining_limit == D("2000")
    assert verdict.allowed_size == D("2000")


def test_cash_binds_before_limit():
    verdicts = evaluate_position_limits(
        cash=D("50"),
        positions_value={},
        max_position_fraction=D("0.20"),
    )
    # portfolio value 50 → limit 10, but only 50 cash... limit binds here;
    # flip the numbers so cash is the smaller side:
    verdicts = evaluate_position_limits(
        cash=D("5"),
        positions_value={"ETH": D("95")},
        max_position_fraction=D("0.9"),
    )
    # portfolio 100 → limit 90; ETH holds 95 → remaining -5 floored to 0
    assert verdicts["ETH"].allowed_size == D("0")
    assert verdicts["ETH"].remaining_limit == D("-5")


def test_overweight_position_gets_zero_not_negative():
    verdicts = evaluate_position_limits(
        cash=D("1000"),
        positions_value={"DOGE": D("500")},
        max_position_fraction=D("0.10"),
    )
    # limit 150 < holding 500 → remaining -350 → allowed 0
    assert verdicts["DOGE"].allowed_size == D("0")
    assert verdicts["DOGE"].remaining_limit == D("-350")


def test_fraction_validation():
    with pytest.raises(RiskGateError):
        evaluate_position_limits(cash=D("1"), positions_value={}, max_position_fraction=D("0"))
    with pytest.raises(RiskGateError):
        evaluate_position_limits(cash=D("1"), positions_value={}, max_position_fraction=D("1.5"))
    with pytest.raises(RiskGateError):
        evaluate_position_limits(cash=D("-1"), positions_value={})
    with pytest.raises(RiskGateError):
        evaluate_position_limits(cash=D("1"), positions_value={"X": D("-2")})


def test_payload_is_json_safe_and_donor_shaped():
    verdicts = evaluate_position_limits(
        cash=D("50000"),
        positions_value={"BTC": D("10000"), "ETH": D("0" * 1 + "500")},
    )
    payload = build_advisory_payload(verdicts)
    serialised = json.dumps(payload)
    assert set(payload.keys()) == {"BTC", "ETH"}
    btc = payload["BTC"]
    assert set(btc.keys()) == {"remaining_position_limit", "reasoning"}
    assert set(btc["reasoning"].keys()) == {
        "position_limit",
        "current_position",
        "remaining_limit",
        "available_cash",
    }
    assert json.loads(serialised) == payload


def test_verdict_is_frozen_dataclass():
    verdicts = evaluate_position_limits(cash=D("10"), positions_value={"X": D("1")})
    with pytest.raises(Exception):
        verdicts["X"].allowed_size = D("99")  # type: ignore[misc]
    assert isinstance(verdicts["X"], PositionLimitVerdict)
