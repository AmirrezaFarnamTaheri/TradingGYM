from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

from trading_gym.execution.accounting import (
    AccountingEvent,
    AccountingEventType,
    FillSide,
    PortfolioLedger,
)
from trading_gym.execution.simulation import (
    ExecutionSimulationPolicy,
    ExecutionSimulationRequest,
    simulate_execution,
)
from trading_gym.execution.governance.ledger import AuditLedger


def _event(account_id: str, event_id: str, event_type: AccountingEventType, **kwargs):
    return AccountingEvent(
        event_id=event_id,
        account_id=account_id,
        event_type=event_type,
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        **kwargs,
    )


def test_accounting_concurrent_unique_events_are_not_lost(tmp_path):
    ledger = PortfolioLedger(
        "concurrent-account",
        path=tmp_path / "account.json",
        ledger=AuditLedger(tmp_path / "audit.jsonl"),
    )
    ledger.create({"USD": Decimal("0")})

    def apply(index: int) -> None:
        ledger.apply(
            _event(
                "concurrent-account",
                f"cash-concurrent-{index:04d}",
                AccountingEventType.CASH_ADJUSTMENT,
                currency="USD",
                amount=Decimal("1"),
            )
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(apply, range(40)))
    snapshot = ledger.snapshot()
    assert snapshot.cash["USD"] == Decimal("40")
    assert snapshot.sequence == 40
    assert ledger.verify()["events"] == 40


def test_accounting_randomised_cash_and_position_invariants(tmp_path):
    rng = random.Random(20260727)
    ledger = PortfolioLedger(
        "random-account",
        path=tmp_path / "account.json",
        ledger=AuditLedger(tmp_path / "audit.jsonl"),
    )
    ledger.create({"USD": Decimal("100000")})
    expected_cash = Decimal("100000")
    expected_quantity = Decimal("0")
    for index in range(100):
        side = FillSide.BUY if rng.random() < 0.5 else FillSide.SELL
        quantity = Decimal(str(rng.randint(1, 20))) / Decimal("10")
        price = Decimal(str(rng.randint(500, 1500))) / Decimal("10")
        fee = Decimal("0.01")
        ledger.apply(
            _event(
                "random-account",
                f"random-fill-{index:04d}",
                AccountingEventType.FILL,
                instrument_id="ASSET-USD",
                quote_currency="USD",
                side=side,
                quantity=quantity,
                price=price,
                fee_amount=fee,
                fee_currency="USD",
            )
        )
        signed = quantity if side is FillSide.BUY else -quantity
        expected_cash -= signed * price + fee
        expected_quantity += signed
    snapshot = ledger.snapshot()
    assert snapshot.cash["USD"] == expected_cash
    assert snapshot.positions["ASSET-USD"].quantity == expected_quantity
    assert snapshot.fees["USD"] == Decimal("1.00")
    assert ledger.verify()["events"] == 100


def test_execution_simulation_randomised_bounds_and_determinism():
    rng = random.Random(20260727)
    for index in range(200):
        side = "buy" if rng.random() < 0.5 else "sell"
        bid = rng.uniform(1.0, 10_000.0)
        ask = bid + rng.uniform(0.0, max(0.01, bid * 0.01))
        quantity = rng.uniform(0.001, 10_000.0)
        request = ExecutionSimulationRequest(
            order_id=f"random-order-{index:04d}",
            instrument_id="ASSET-USD",
            side=side,
            order_type="market",
            quantity=quantity,
            bid_price=bid,
            ask_price=ask,
            interval_volume_units=rng.uniform(0.0, 50_000.0),
            visible_liquidity_units=rng.uniform(0.0, 50_000.0),
            volatility_fraction=rng.uniform(0.0, 0.05),
        )
        policy = ExecutionSimulationPolicy(seed=index, max_participation_fraction=0.2)
        first = simulate_execution(request, policy)
        second = simulate_execution(request, policy)
        assert first == second
        assert 0.0 <= first.filled_quantity <= request.quantity
        assert (
            abs(first.filled_quantity + first.unfilled_quantity - request.quantity)
            < 1e-8
        )
        assert first.total_cost >= 0.0
        assert not first.execution_authority_granted
        if first.execution_price is not None:
            if side == "buy":
                assert first.execution_price >= ask
            else:
                assert first.execution_price <= bid
