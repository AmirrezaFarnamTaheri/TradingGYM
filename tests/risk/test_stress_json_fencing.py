"""Insolvent simulated portfolio stress reports must remain finite JSON."""

import hashlib
import json
import math

from trading_gym.risk.stress import (
    StressPosition, StressScenario, evaluate_portfolio_stress,
)


def scenario(shock=-1.0):
    return StressScenario(
        scenario_id="stress:insolvent-0001", label="equity wipeout",
        default_price_shock_fraction=shock,
    )


def position(notional=1000):
    return StressPosition(
        instrument_id="BTC-USDT", asset_class="crypto", currency="USD",
        notional_base=notional,
    )


def test_insolvent_portfolio_is_unambiguously_blocked_without_infinity():
    report = evaluate_portfolio_stress(
        equity=100, positions=[position()], scenario=scenario(),
    )
    assert report.allowed is False
    assert report.equity_after < 0
    assert "minimum_equity" in report.violations
    assert "gross_leverage_unavailable" in report.violations
    assert report.post_stress_gross_leverage is None
    assert report.execution_authority_granted is False
    wire = json.loads(report.model_dump_json())
    assert wire["post_stress_gross_leverage"] is None
    assert math.isfinite(wire["loss_fraction"])


def test_near_zero_account_cannot_serialize_infinite_leverage():
    report = evaluate_portfolio_stress(
        equity=1e-300, positions=[position(notional=1e30)], scenario=scenario(0),
    )
    assert report.allowed is False
    assert "gross_leverage_unavailable" in report.violations
    assert report.post_stress_gross_leverage is None
    assert "Infinity" not in report.model_dump_json()
    assert "NaN" not in report.model_dump_json()


def test_regular_stress_retains_finite_leverage_and_content_bound_hash():
    report = evaluate_portfolio_stress(
        equity=1000, positions=[position(50)], scenario=scenario(-0.10),
    )
    assert report.allowed is True
    assert report.post_stress_gross_leverage == 45 / 995
    assert report.report_sha256
    payload = report.model_dump(mode="json", exclude={
        "allowed", "report_sha256", "mutation_performed", "execution_authority_granted",
    })
    check = json.dumps(
        payload, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode()
    assert hashlib.sha256(check).hexdigest() == report.report_sha256


def test_tiny_equity_with_large_losses_cannot_emit_infinite_loss_fraction():
    report = evaluate_portfolio_stress(
        equity=1e-300, positions=[position(notional=1e30)],
        scenario=scenario(-1),
    )
    assert report.loss_fraction is None
    assert "loss_fraction_unavailable" in report.violations
    assert report.allowed is False
    assert "Infinity" not in report.model_dump_json()


def test_boolean_equity_is_not_a_valid_account_balance():
    import pytest

    with pytest.raises(ValueError, match="equity"):
        evaluate_portfolio_stress(
            equity=True, positions=[], scenario=scenario(0),
        )
