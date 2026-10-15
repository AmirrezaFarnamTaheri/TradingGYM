"""Read-only order/fill/broker parity evidence from peer trading systems."""

from decimal import Decimal as D

import pytest

from trading_gym.convergence.peer_execution_parity import (
    FillEvidence,
    OrderIntent,
    VenueReadback,
    reconcile_peer_execution,
)


def intent(**changes):
    values = dict(
        order_id="order-001",
        client_order_id="client-001",
        instrument_id="BTC-USDT",
        side="buy",
        order_type="post_only",
        quantity=D("2.0"),
        lot_step=D("0.1"),
        price_tick=D("0.01"),
        position_before=D("1"),
        cash_before_base=D("1000"),
        submitted_at_unix=100,
    )
    values.update(changes)
    return OrderIntent(**values)


def fill(**changes):
    values = dict(
        execution_id="exec-001",
        order_id="order-001",
        sequence=1,
        observed_at_unix=101,
        quantity=D("0.5"),
        price=D("100.00"),
        fee_base=D("-0.01"),
        liquidity="maker",
    )
    values.update(changes)
    return FillEvidence(**values)


def readback(**changes):
    values = dict(
        order_id="order-001",
        observed_at_unix=102,
        status="cancelled",
        cumulative_filled=D("0.5"),
        position_qty=D("1.5"),
        cash_base=D("950.01"),
        complete=True,
        provenance_sha256="a" * 64,
        scope="isolated_order",
    )
    values.update(changes)
    return VenueReadback(**values)


def evaluate(order=None, fills=None, broker=None):
    return reconcile_peer_execution(
        order or intent(),
        fills if fills is not None else [fill()],
        broker or readback(),
        as_of_unix=110,
    )


def test_partial_post_only_fill_and_cancel_is_matched_but_not_trade_authority():
    report = evaluate()
    assert report["status"] == "matched"
    assert report["order_finalized"] is True
    assert report["duplicate_events_ignored"] == 0
    assert report["computed_position_qty"] == "1.5"
    assert report["computed_cash_base"] == "950.01"
    assert report["execution_authority_granted"] is False


def test_repeated_identical_fill_is_idempotent_and_does_not_double_account():
    report = evaluate(fills=[fill(), fill()])
    assert report["status"] == "matched"
    assert report["duplicate_events_ignored"] == 1
    assert report["computed_cash_base"] == "950.01"


def test_colliding_execution_id_is_denied():
    with pytest.raises(ValueError, match="collision"):
        evaluate(fills=[fill(), fill(price=D("100.01"))])


def test_missing_sequence_is_denied_instead_of_counting_complete():
    with pytest.raises(ValueError, match="sequence"):
        evaluate(fills=[fill(sequence=2)])


def test_order_id_mismatch_and_future_fill_time_denied():
    with pytest.raises(ValueError, match="order"):
        evaluate(fills=[fill(order_id="foreign")])
    with pytest.raises(ValueError, match="timestamp"):
        evaluate(fills=[fill(observed_at_unix=103)])


def test_post_only_taker_fill_denied_before_matched_readback():
    with pytest.raises(PermissionError, match="post_only"):
        evaluate(fills=[fill(liquidity="taker")])


def test_venue_cash_drift_never_counts_as_reconciled():
    report = evaluate(broker=readback(cash_base=D("950.00")))
    assert report["status"] == "drift"
    assert "cash" in report["differences"]
    assert report["execution_authority_granted"] is False


def test_ioc_partial_cancel_taker_and_sell_with_fee():
    report = evaluate(
        order=intent(
            order_type="ioc",
            side="sell",
            position_before=D("2"),
            cash_before_base=D("0"),
        ),
        fills=[fill(liquidity="taker", fee_base=D("0.1"))],
        broker=readback(position_qty=D("1.5"), cash_base=D("49.9")),
    )
    assert report["status"] == "matched"
    assert report["order_finalized"] is True


def test_never_accept_claimed_full_fill_that_exceeds_order_quantity():
    with pytest.raises(ValueError, match="quantity"):
        evaluate(fills=[fill(quantity=D("2.1"))])


def test_lot_and_tick_violation_denied():
    with pytest.raises(ValueError, match="lot"):
        evaluate(fills=[fill(quantity=D("0.55"))])
    with pytest.raises(ValueError, match="tick"):
        evaluate(fills=[fill(price=D("100.001"))])


def test_stale_incomplete_or_unsourced_broker_readback_is_denied():
    for snapshot in [
        readback(complete=False),
        readback(scope="aggregated_account"),
        readback(observed_at_unix=120),
        readback(provenance_sha256="not-a-hash"),
    ]:
        with pytest.raises(ValueError):
            evaluate(broker=snapshot)


def test_terminal_filled_order_requires_full_quantity_and_complete_receipts():
    with pytest.raises(ValueError, match="filled"):
        evaluate(broker=readback(status="filled"))
    second = fill(
        execution_id="exec-002",
        sequence=2,
        quantity=D("1.5"),
        observed_at_unix=102,
        fee_base=D("0.03"),
    )
    report = evaluate(
        fills=[fill(), second],
        broker=readback(
            status="filled",
            observed_at_unix=103,
            cumulative_filled=D("2.0"),
            position_qty=D("3.0"),
            cash_base=D("799.98"),
        ),
    )
    assert report["status"] == "matched"
    assert report["order_finalized"] is True


def test_unsettled_open_order_never_claims_finality():
    report = evaluate(broker=readback(status="open"))
    assert report["status"] == "matched"
    assert report["order_finalized"] is False


def test_paired_replay_digest_is_stable_for_identical_fill_retries():
    first = evaluate()
    repeated = evaluate(fills=[fill(), fill()])
    assert first["evidence_sha256"] == repeated["evidence_sha256"]


def test_unknown_or_non_decimal_numeric_data_is_denied():
    with pytest.raises(ValueError):
        evaluate(order=intent(quantity=2.0))
    with pytest.raises(ValueError):
        evaluate(fills=[fill(fee_base=D("NaN"))])

def test_pathological_decimal_scale_denied_with_stable_domain_error():
    # The default Decimal context cannot divide 1e28 by 1e-28 exactly;
    # a controlled denial is required, not raw InvalidOperation leakage.
    with pytest.raises(ValueError, match="increment|precision|lot"):
        evaluate(order=intent(quantity=D("1e28"), lot_step=D("1e-28")))


def test_large_decimal_fill_uses_exact_cash_product_not_context_rounding():
    from decimal import localcontext

    quantity = D("123456789.123456")
    price = D("987654321.987654")
    before = D("200000000000000000")
    with localcontext() as context:
        context.prec = 80
        expected = before - quantity * price - D("0.01")
    report = evaluate(
        order=intent(
            quantity=quantity,
            lot_step=D("0.000001"),
            price_tick=D("0.000001"),
            cash_before_base=before,
        ),
        fills=[fill(quantity=quantity, price=price, fee_base=D("0.01"))],
        broker=readback(
            status="filled",
            cumulative_filled=quantity,
            position_qty=D("1") + quantity,
            cash_base=expected,
        ),
    )
    assert report["status"] == "matched"
    assert report["computed_cash_base"] == format(expected.normalize(), "f")
