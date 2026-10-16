"""Pre-trade identity controls must reject malformed inputs, not crash."""

from decimal import Decimal as D

import pytest

from trading_gym.production.pre_trade_gate import evaluate_pre_trade_gate


def gate(**updates):
    arguments = dict(
        operation="order.cancel",
        quantity=D("0"),
        idempotency_key="a" * 32,
        reference_id="order-123",
    )
    arguments.update(updates)
    return evaluate_pre_trade_gate(**arguments)


def test_valid_cancel_passes_pure_gate():
    report = gate()
    assert report.allowed
    assert len(report.gate_id) == 16


@pytest.mark.parametrize(
    "reference_id",
    ["", " ", "\t", "\n", "order 123", 123, None],
)
def test_blank_or_noncanonical_cancel_reference_denied(reference_id):
    verdict = gate(reference_id=reference_id)
    assert not verdict.allowed
    assert "reference_present" in verdict.denial_reasons


@pytest.mark.parametrize("key", [True, 123, [], {}, None])
def test_invalid_idempotency_types_deny_instead_of_crashing(key):
    verdict = gate(idempotency_key=key)
    assert not verdict.allowed
    assert "idempotency_key_format" in verdict.denial_reasons


def test_canonical_spot_market_keeps_optional_reference_behavior():
    verdict = gate(
        operation="spot.market",
        quantity=D("0.1"),
        reference_id="",
    )
    assert verdict.allowed


def test_unhashable_operation_returns_denial_without_exception():
    verdict = gate(operation=[])
    assert not verdict.allowed
    assert "operation_known" in verdict.denial_reasons
