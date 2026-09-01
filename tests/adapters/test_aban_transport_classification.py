"""AbanTransport failure classification tests (kraken-taxonomy absorption)."""

from decimal import Decimal

import pytest

from trading_gym.adapters.aban_transport import (
    AbanTransport,
    operation_is_idempotent,
)
from trading_gym.production.mutation_retry import MutationOutcomeUnknown
from trading_gym.production.venues import VenueOrderIntent


def _intent(operation: str, key: str = "test-key-1") -> VenueOrderIntent:
    return VenueOrderIntent(
        venue="abantether",
        operation=operation,
        base_symbol="BTC",
        quote_symbol="USDT",
        side="buy",
        quantity=Decimal("0.5"),
        idempotency_key=key,
        reference_id="ref-9" if operation in {"short.close", "order.cancel"} else "",
    )


class _FlakyClient:
    def __init__(self, error: Exception):
        self.error = error

    def place_market_order(self, *args, **kwargs):
        raise self.error

    def cancel_order(self, order_id):
        raise self.error


def test_operation_is_idempotent_only_for_cancel():
    assert operation_is_idempotent("order.cancel") is True
    assert operation_is_idempotent("spot.market") is False
    assert operation_is_mutation_safe_shortcut()


def operation_is_mutation_safe_shortcut() -> bool:
    # spot/short operations must never be flagged idempotent.
    return not any(
        operation_is_idempotent(op) for op in ("spot.market", "short.open", "short.close")
    )


def test_unknown_outcome_wraps_non_idempotent_operations():
    transport = AbanTransport(_FlakyClient(RuntimeError("read timeout after send")))
    with pytest.raises(MutationOutcomeUnknown):
        transport.submit_order(_intent("spot.market"))


def test_idempotent_cancel_keeps_original_error_for_retry():
    boom = RuntimeError("read timeout after send")
    transport = AbanTransport(_FlakyClient(boom))
    with pytest.raises(RuntimeError) as excinfo:
        transport.submit_order(_intent("order.cancel"))
    # The original error propagates (still retryable via the policy's
    # unknown-outcome/pre-send classes) and is NOT wrapped as ambiguous,
    # so the coordinator does not taint the idempotency key.
    assert excinfo.value is boom


def test_local_validation_stays_terminal():
    class _OkClient:
        def cancel_order(self, order_id):
            raise ValueError("order.cancel requires reference_id")

    transport = AbanTransport(_OkClient())
    intent = VenueOrderIntent(
        venue="abantether",
        operation="order.cancel",
        base_symbol="BTC",
        quote_symbol="USDT",
        side="none",
        quantity=Decimal("0"),
        idempotency_key="k",
        reference_id="",
    )
    with pytest.raises(ValueError):
        transport.submit_order(intent)
