"""Independent order/fill/readback parity regression cases (no broker calls)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from trading_gym.accounting.order_fill_parity import (
    BrokerReadback,
    FillEvidence,
    OrderIntent,
    reconcile_order_fills,
)

T = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def intent(**changes):
    values = dict(
        order_id="order001",
        account_id="ACC001",
        instrument_id="BTC-USD",
        venue_id="VENUE1",
        quote_currency="USD",
        side="buy",
        quantity=D("2"),
        quantity_step=D("0.1"),
        price_tick=D("0.01"),
        submitted_at=T,
        time_in_force="ioc",
        post_only=False,
        fee_ceiling_bps=D("30"),
        idempotency_key="a" * 32,
    )
    values.update(changes)
    return OrderIntent(**values)


def fill(fill_id="fill001", **changes):
    values = dict(
        fill_id=fill_id,
        order_id="order001",
        account_id="ACC001",
        venue_id="VENUE1",
        instrument_id="BTC-USD",
        occurred_at=T + timedelta(seconds=1),
        quantity=D("0.8"),
        price=D("100.00"),
        fee_quote=D("0.08"),
        liquidity="taker",
    )
    values.update(changes)
    return FillEvidence(**values)


def readback(**changes):
    values = dict(
        order_id="order001",
        account_id="ACC001",
        observed_at=T + timedelta(seconds=2),
        state="canceled",
        executed_quantity=D("0.8"),
        total_fee_quote=D("0.08"),
        source_sha256="e" * 64,
    )
    values.update(changes)
    return BrokerReadback(**values)


def report(order=None, fills=None, readbacks=None, position=None, cash=None):
    return reconcile_order_fills(
        as_of=T + timedelta(seconds=5),
        orders=[order or intent()],
        fills=[fill()] if fills is None else fills,
        readbacks=[readback()] if readbacks is None else readbacks,
        position_deltas={"BTC-USD": D("0.8")} if position is None else position,
        cash_deltas={"USD": D("-80.08")} if cash is None else cash,
    )


def test_ioc_partial_then_canceled_is_consistent_and_read_only():
    result = report()
    assert result.consistent
    assert result.blockers == []
    assert result.executed_quantity_by_instrument == {"BTC-USD": D("0.8")}
    assert result.net_cash_by_currency == {"USD": D("-80.08")}
    assert result.reconciled_execution_count == 1
    assert result.broker_mutation_performed is False
    assert result.execution_authority_granted is False
    assert len(result.report_sha256) == 64


def test_duplicate_fill_or_missing_readback_is_denied():
    with pytest.raises(ValueError, match="duplicate fill"):
        report(fills=[fill(), fill()])
    assert "readback_missing:order001" in report(readbacks=[]).blockers


def test_fill_quantity_and_fee_must_equal_broker_readback():
    assert (
        "quantity_mismatch:order001"
        in report(readbacks=[readback(executed_quantity=D("1.2"))]).blockers
    )
    assert (
        "fee_mismatch:order001"
        in report(readbacks=[readback(total_fee_quote=D("0.09"))]).blockers
    )


def test_position_and_cash_snapshot_provenance_required():
    assert (
        "position_delta_mismatch:BTC-USD"
        in report(position={"BTC-USD": D("0")}).blockers
    )
    assert "cash_delta_mismatch:USD" in report(cash={"USD": D("0")}).blockers
    assert "position_readback_missing" in report(position={}).blockers
    assert "cash_readback_missing" in report(cash={}).blockers


def test_post_only_cannot_have_taker_execution():
    order = intent(post_only=True, time_in_force="gtc")
    assert "post_only_taker_fill:order001" in report(order=order).blockers


def test_fok_partial_execution_is_inconsistent_even_if_canceled():
    assert (
        "fok_partial_execution:order001"
        in report(order=intent(time_in_force="fok")).blockers
    )


def test_ioc_cannot_remain_open_after_readback():
    assert (
        "nonterminal_ioc:order001"
        in report(readbacks=[readback(state="open")]).blockers
    )


def test_wrong_venue_or_price_tick_blocks_reconciliation():
    assert "venue_mismatch:fill001" in report(fills=[fill(venue_id="VENUE2")]).blockers
    assert (
        "invalid_price_tick:fill001"
        in report(fills=[fill(price=D("100.001"))]).blockers
    )


def test_quote_fee_ceiling_rejects_unexplained_commission():
    result = report(
        fills=[fill(fee_quote=D("2"))],
        readbacks=[readback(total_fee_quote=D("2"))],
        cash={"USD": D("-82")},
    )
    assert "excess_fee:order001" in result.blockers


def test_future_readback_and_fill_before_order_rejected():
    with pytest.raises(ValueError, match="future"):
        report(readbacks=[readback(observed_at=T + timedelta(seconds=9))])
    with pytest.raises(ValueError, match="before submission"):
        report(fills=[fill(occurred_at=T - timedelta(seconds=1))])


def test_order_independent_receipt_binds_to_evidence_hashes():
    a = intent()
    b = intent(order_id="order002", idempotency_key="b" * 32)
    a_fill = fill()
    b_fill = fill(
        fill_id="fill002",
        order_id="order002",
        quantity=D("0.5"),
        fee_quote=D("0.05"),
    )
    a_read = readback()
    b_read = readback(
        order_id="order002",
        executed_quantity=D("0.5"),
        total_fee_quote=D("0.05"),
        source_sha256="f" * 64,
    )
    kwargs = dict(
        as_of=T + timedelta(seconds=5),
        position_deltas={"BTC-USD": D("1.3")},
        cash_deltas={"USD": D("-130.13")},
    )
    first = reconcile_order_fills(
        orders=[a, b],
        fills=[a_fill, b_fill],
        readbacks=[a_read, b_read],
        **kwargs,
    )
    second = reconcile_order_fills(
        orders=[b, a],
        fills=[b_fill, a_fill],
        readbacks=[b_read, a_read],
        **kwargs,
    )
    assert first == second and first.consistent
    altered = reconcile_order_fills(
        orders=[b, a],
        fills=[b_fill, a_fill],
        readbacks=[b_read.model_copy(update={"source_sha256": "0" * 64}), a_read],
        **kwargs,
    )
    assert altered.report_sha256 != first.report_sha256


def test_duplicate_idempotency_key_cannot_be_reconciled():
    with pytest.raises(ValueError, match="duplicate idempotency"):
        reconcile_order_fills(
            as_of=T + timedelta(seconds=5),
            orders=[intent(), intent(order_id="order002")],
            fills=[],
            readbacks=[],
            position_deltas={},
            cash_deltas={},
        )


def test_wrong_account_fill_or_broker_readback_cannot_be_reconciled():
    result = report(fills=[fill(account_id="ACC002")])
    assert "account_mismatch:fill001" in result.blockers
    assert result.consistent is False
    assert result.account_id == "ACC001"

    result = report(readbacks=[readback(account_id="ACC002")])
    assert "readback_account_mismatch:order001" in result.blockers
    assert result.execution_authority_granted is False


def test_mixed_custody_accounts_cannot_share_cash_and_position_readbacks():
    with pytest.raises(ValueError, match="mixed accounts"):
        reconcile_order_fills(
            as_of=T + timedelta(seconds=5),
            orders=[
                intent(order_id="order001", account_id="ACC001"),
                intent(
                    order_id="order002",
                    account_id="ACC002",
                    idempotency_key="b" * 32,
                ),
            ],
            fills=[],
            readbacks=[],
            position_deltas={},
            cash_deltas={},
        )


def test_accounting_public_api_exposes_read_only_parity_without_live_executor():
    from trading_gym import accounting

    assert accounting.OrderIntent is OrderIntent
    assert accounting.FillEvidence is FillEvidence
    assert accounting.BrokerReadback is BrokerReadback
    assert accounting.reconcile_order_fills is reconcile_order_fills
