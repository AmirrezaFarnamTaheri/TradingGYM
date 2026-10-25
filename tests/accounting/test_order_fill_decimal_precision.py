"""Large valid Decimal fills cannot be rounded into false account drift."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, localcontext

import pytest

from trading_gym.execution.accounting.order_fill_parity import (
    BrokerReadback,
    FillEvidence,
    OrderIntent,
    reconcile_order_fills,
)

T = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def test_exact_cash_and_notional_with_large_decimal_fill():
    qty = D("123456789.123456789")
    price = D("987654321.987654321")
    fee = D("0.00000001")
    with localcontext() as context:
        context.prec = 96
        expected_cash = -(qty * price + fee)
    order = OrderIntent(
        order_id="order001",
        account_id="ACC001",
        instrument_id="BTC-USD",
        venue_id="VENUE1",
        quote_currency="USD",
        side="buy",
        quantity=qty,
        quantity_step=D("0.000000001"),
        price_tick=D("0.000000001"),
        submitted_at=T,
        time_in_force="ioc",
        fee_ceiling_bps=D("100"),
        idempotency_key="a" * 32,
    )
    execution = FillEvidence(
        fill_id="fill001",
        order_id=order.order_id,
        account_id=order.account_id,
        venue_id=order.venue_id,
        instrument_id=order.instrument_id,
        occurred_at=T + timedelta(seconds=1),
        quantity=qty,
        price=price,
        fee_quote=fee,
        liquidity="taker",
    )
    venue = BrokerReadback(
        order_id=order.order_id,
        account_id=order.account_id,
        observed_at=T + timedelta(seconds=2),
        state="filled",
        executed_quantity=qty,
        total_fee_quote=fee,
        source_sha256="f" * 64,
    )
    report = reconcile_order_fills(
        as_of=T + timedelta(seconds=5),
        orders=[order],
        fills=[execution],
        readbacks=[venue],
        position_deltas={"BTC-USD": qty},
        cash_deltas={"USD": expected_cash},
    )
    assert report.consistent is True
    assert report.blockers == []
    assert report.net_cash_by_currency["USD"] == expected_cash
    assert report.reconciled_execution_count == 1
    assert report.execution_authority_granted is False


def test_large_decimal_evidence_remains_unauthorized_even_when_matched():
    qty = D("123456789.123456789")
    price = D("987654321.987654321")
    with localcontext() as context:
        context.prec = 96
        expected_cash = qty * price
    result = reconcile_order_fills(
        as_of=T + timedelta(seconds=5),
        orders=[
            OrderIntent(
                order_id="order002",
                account_id="ACC001",
                instrument_id="BTC-USD",
                venue_id="VENUE1",
                quote_currency="USD",
                side="sell",
                quantity=qty,
                quantity_step=D("0.000000001"),
                price_tick=D("0.000000001"),
                submitted_at=T,
                time_in_force="ioc",
                idempotency_key="b" * 32,
            )
        ],
        fills=[
            FillEvidence(
                fill_id="fill002",
                order_id="order002",
                account_id="ACC001",
                venue_id="VENUE1",
                instrument_id="BTC-USD",
                occurred_at=T + timedelta(seconds=1),
                quantity=qty,
                price=price,
                fee_quote=D("0"),
                liquidity="taker",
            )
        ],
        readbacks=[
            BrokerReadback(
                order_id="order002",
                account_id="ACC001",
                observed_at=T + timedelta(seconds=2),
                state="filled",
                executed_quantity=qty,
                total_fee_quote=D("0"),
                source_sha256="e" * 64,
            )
        ],
        position_deltas={"BTC-USD": -qty},
        cash_deltas={"USD": expected_cash},
    )
    assert result.consistent and result.net_cash_by_currency["USD"] == expected_cash
    assert result.execution_authority_granted is False


@pytest.mark.parametrize(
    "pathological",
    [
        D("1e-1000"),
        D("0." + "0" * 999 + "1"),
    ],
)
def test_decimal_inputs_have_bounded_scale_before_step_modulus(pathological):
    with pytest.raises(ValueError, match="precision"):
        OrderIntent(
            order_id="order003",
            account_id="ACC001",
            instrument_id="BTC-USD",
            venue_id="VENUE1",
            quote_currency="USD",
            side="buy",
            quantity=pathological,
            quantity_step=pathological,
            price_tick=D("0.01"),
            submitted_at=T,
            time_in_force="ioc",
            idempotency_key="c" * 32,
        )
