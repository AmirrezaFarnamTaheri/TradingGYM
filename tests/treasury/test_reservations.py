"""Reservation ledger tests (kraken balance-reservation absorption)."""

from decimal import Decimal

import pytest

from trading_gym.treasury.reservations import ReservationError, ReservationLedger

D = Decimal


@pytest.fixture
def ledger():
    return ReservationLedger({"USDT": D("10_000"), "BTC": D("1")})


def test_reserve_reduces_available_and_snapshot_reports_it(ledger):
    ledger.reserve(order_id="ord-1", currency="usdt", amount=D("4_000"))
    snap = ledger.snapshot()
    assert snap["USDT"]["reserved"] == D("4_000")
    assert snap["USDT"]["available"] == D("6_000")


def test_over_reservation_is_refused(ledger):
    with pytest.raises(ReservationError):
        ledger.reserve(order_id="ord-1", currency="USDT", amount=D("10_001"))


def test_double_reservation_same_order_refused(ledger):
    ledger.reserve(order_id="ord-1", currency="USDT", amount=D("100"))
    with pytest.raises(ReservationError):
        ledger.reserve(order_id="ord-1", currency="USDT", amount=D("100"))


def test_release_is_idempotent_and_restores_availability(ledger):
    ledger.reserve(order_id="ord-1", currency="USDT", amount=D("2_500"))
    assert ledger.release(order_id="ord-1") is True
    assert ledger.release(order_id="ord-1") is False
    assert ledger.available_balance("USDT") == D("10_000")


def test_commit_settles_within_reservation_without_topup(ledger):
    ledger.reserve(order_id="ord-1", currency="USDT", amount=D("5_025"))
    topup = ledger.commit_settlement(
        order_id="ord-1", currency="USDT", settled_amount=D("5_000")
    )
    assert topup == D("0")
    snap = ledger.snapshot()
    assert snap["USDT"]["committed"] == D("5_000")
    assert snap["USDT"]["available"] == D("5_000")
    # reservation released by settlement
    assert ledger.reserved_for_order("ord-1") == {}


def test_commit_above_reservation_takes_topup_from_available(ledger):
    ledger.reserve(order_id="ord-1", currency="USDT", amount=D("5_000"))
    topup = ledger.commit_settlement(
        order_id="ord-1", currency="USDT", settled_amount=D("5_100")
    )
    assert topup == D("100")
    assert ledger.snapshot()["USDT"]["committed"] == D("5_100")


def test_commit_beyond_reservation_plus_available_refused():
    ledger = ReservationLedger({"USDT": D("100")})
    ledger.reserve(order_id="o", currency="USDT", amount=D("90"))
    with pytest.raises(ReservationError):
        ledger.commit_settlement(order_id="o", currency="USDT", settled_amount=D("200"))


def test_commit_requires_active_reservation(ledger):
    with pytest.raises(ReservationError):
        ledger.commit_settlement(
            order_id="ghost", currency="USDT", settled_amount=D("1")
        )


def test_unknown_currency_refused(ledger):
    with pytest.raises(ReservationError):
        ledger.reserve(order_id="o", currency="NOPE", amount=D("1"))


def test_negative_opening_balance_and_amounts_refused():
    with pytest.raises(ValueError):
        ReservationLedger({"USDT": D("-1")})
    ledger = ReservationLedger({"USDT": D("50")})
    with pytest.raises(ValueError):
        ledger.reserve(order_id="o", currency="USDT", amount=D("-5"))


def test_event_digest_is_deterministic_and_order_sensitive(ledger):
    d1 = ledger.events_digest()
    assert d1 == ledger.events_digest()
    ledger.reserve(order_id="a", currency="USDT", amount=D("1"))
    other = ReservationLedger({"USDT": D("10_000")})
    other.reserve(order_id="a", currency="USDT", amount=D("1"))
    assert (
        other.events_digest()
        != ReservationLedger({"USDT": D("10_000")}).events_digest()
    )
    del d1


def test_cross_currency_isolation(ledger):
    ledger.reserve(order_id="btc-o", currency="BTC", amount=D("0.5"))
    assert ledger.available_balance("USDT") == D("10_000")
    assert ledger.available_balance("BTC") == D("0.5")


@pytest.mark.parametrize("amount", [D("NaN"), D("Infinity"), D("-Infinity")])
def test_nonfinite_money_never_enters_reservation_ledger(amount):
    with pytest.raises(ValueError, match="finite non-negative"):
        ReservationLedger({"USDT": amount})

    ledger = ReservationLedger({"USDT": D("100")})
    with pytest.raises(ValueError, match="finite positive"):
        ledger.reserve(order_id="bad", currency="USDT", amount=amount)
    ledger.reserve(order_id="valid", currency="USDT", amount=D("10"))
    with pytest.raises(ValueError, match="finite positive"):
        ledger.commit_settlement(
            order_id="valid", currency="USDT", settled_amount=amount
        )
    assert ledger.available_balance("USDT") == D("90")
    assert ledger.reserved_for_order("valid")["USDT"] == D("10")


def test_order_id_cannot_be_reused_after_release_or_settlement(ledger):
    ledger.reserve(order_id="release-1", currency="USDT", amount=D("100"))
    assert ledger.release(order_id="release-1") is True
    with pytest.raises(ReservationError, match="already used"):
        ledger.reserve(order_id="release-1", currency="USDT", amount=D("100"))

    ledger.reserve(order_id="settled-1", currency="USDT", amount=D("200"))
    ledger.commit_settlement(
        order_id="settled-1", currency="USDT", settled_amount=D("210")
    )
    before = ledger.events_digest()
    with pytest.raises(ReservationError, match="already used"):
        ledger.reserve(order_id="settled-1", currency="USDT", amount=D("200"))
    assert ledger.events_digest() == before
    assert ledger.snapshot()["USDT"]["committed"] == D("210")


def test_ledger_rejects_duplicate_currency_after_case_normalization():
    with pytest.raises(ValueError, match="duplicate_normalized_currency"):
        ReservationLedger({"usdt": D("100"), "USDT": D("200")})


def test_concurrent_reservations_cannot_overcommit_same_currency():
    from concurrent.futures import ThreadPoolExecutor
    from time import sleep

    ledger = ReservationLedger({"USDT": D("100")})
    read_balance = ledger.available_balance

    def slow_read(currency):
        amount = read_balance(currency)
        sleep(0.03)
        return amount

    ledger.available_balance = slow_read

    def reserve(index):
        try:
            ledger.reserve(
                order_id=f"concurrent-{index}",
                currency="USDT",
                amount=D("70"),
            )
        except ReservationError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(reserve, range(2)))
    assert results.count(True) == 1
    assert results.count(False) == 1
    assert ledger.snapshot()["USDT"]["reserved"] == D("70")
    assert ledger.snapshot()["USDT"]["available"] == D("30")
