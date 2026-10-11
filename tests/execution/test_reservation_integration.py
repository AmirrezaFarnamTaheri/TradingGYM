"""Balance-reservation integration in the live execution adapter.

Kraken-paper semantics wired end-to-end: a spending mutation locks
cost×(1+fee) before any venue contact; the lock is settled when the
coordinator reports a committed receipt, released when the failure is
provably terminal, and held while the outcome is ambiguous.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from trading_gym.adapters.enhanced_aban_client import (
    Balance,
    EnhancedAbanClient,
    OrderResponse,
    OrderSide,
    OrderStatus,
)
from trading_gym.execution.live_execution_adapter import LiveExecutionAdapter
from trading_gym.production.mutation_retry import (
    MutationOutbox,
    MutationOutcomeUnknown,
    MutationRetryPolicy,
)
from axiomforge.live_trading import LiveConsent

D = Decimal


def _order(order_id: str, side: OrderSide) -> OrderResponse:
    return OrderResponse(
        id=order_id,
        code="200",
        base_symbol="BTC",
        quote_symbol="USDT",
        side=side,
        type="market",
        status=OrderStatus.FILLED,
        volume={"USDT": D("100")},
        filled_volume={"BTC": D("1.0")},
        created_at="now",
    )


@pytest.fixture(autouse=True)
def isolated_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))


@pytest.fixture
def config():
    cfg = MagicMock()
    cfg.env.symbol = "BTCUSDT"
    cfg.env.quote_ccy = "USDT"
    cfg.env.decimals.qty_for.return_value = 6
    cfg.env.decimals.price_for.return_value = 2
    cfg.env.decimals.min_qty_for.return_value = 0.001
    cfg.env.cost.taker_fee_bps = 10.0
    return cfg


@pytest.fixture
def client():
    cl = MagicMock(spec=EnhancedAbanClient)
    cl.get_balance.return_value = [
        Balance(symbol="USDT", balance=D("100000"), blocked=D("0")),
        Balance(symbol="BTC", balance=D("5"), blocked=D("0")),
    ]
    cl.get_ticker.return_value = {}
    cl.get_short_positions.return_value = []
    return cl


def _adapter(cfg, cl, **kwargs):
    with patch(
        "trading_gym.execution.live_execution_adapter.EnhancedAbanClient",
        return_value=cl,
    ):
        return LiveExecutionAdapter(
            cfg,
            "fake_token",
            live_consent=LiveConsent(
                actor="test-executor",
                scope="live.execute",
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                nonce="reservation-test",
            ),
            proposal_id="res-proposal",
            mutation_policy=MutationRetryPolicy(
                max_attempts=kwargs.get("max_attempts", 3),
                retryable_exceptions=(
                    MutationOutcomeUnknown,
                    TimeoutError,
                    ConnectionError,
                ),
            ),
            **kwargs.get("extra", {}),
        )


@pytest.mark.asyncio
async def test_buy_reserves_then_settles(config, client):
    adapter = _adapter(config, client)
    adapter._get_net_position = MagicMock(return_value=D("0.0"))
    client.place_market_order.return_value = _order("o1", OrderSide.BUY)

    res = await adapter.reconcile(1.0, 50000.0)
    assert res["status"] == "executed"

    ledger = adapter._ensure_reservation_ledger()
    # Cost incl. 10 bps fee committed against opening USDT.
    assert ledger.snapshot()["USDT"]["committed"] == D("50050")
    assert ledger.snapshot()["USDT"]["available"] == D("49950")
    assert ledger.snapshot()["USDT"]["reserved"] == D("0")


@pytest.mark.asyncio
async def test_insufficient_balance_aborts_before_venue(config, client):
    client.get_balance.return_value = [
        Balance(symbol="USDT", balance=D("100"), blocked=D("0")),
    ]
    adapter = _adapter(config, client)
    adapter._get_net_position = MagicMock(return_value=D("0.0"))

    res = await adapter.reconcile(1.0, 50000.0)

    assert res["status"] == "failed"
    assert res["error_type"] == "ReservationError"
    client.place_market_order.assert_not_called()
    ledger = adapter._ensure_reservation_ledger()
    assert ledger.snapshot()["USDT"]["reserved"] == D("0")
    assert ledger.snapshot()["USDT"]["committed"] == D("0")


@pytest.mark.asyncio
async def test_sdk_response_validation_error_is_ambiguous_and_keeps_reservation(config, client):
    adapter = _adapter(config, client)
    adapter._get_net_position = MagicMock(return_value=D("0.0"))
    client.place_market_order.side_effect = ValueError("bad payload")

    res = await adapter.reconcile(1.0, 50000.0)
    assert res["status"] == "failed"
    assert res["error_type"] == "MutationOutcomeUnknown"

    snap = adapter._ensure_reservation_ledger().snapshot()["USDT"]
    assert snap["reserved"] == D("50050")
    assert snap["committed"] == D("0")
    assert snap["available"] == D("49950")


@pytest.mark.parametrize("status", [OrderStatus.NEW, OrderStatus.PROCESSING, OrderStatus.OPEN])
@pytest.mark.asyncio
async def test_acknowledged_but_unfilled_order_keeps_reservation_and_blocks_next_order(
    config, client, status
):
    adapter = _adapter(config, client)
    adapter._get_net_position = MagicMock(return_value=D("0"))
    response = _order("unfilled", OrderSide.BUY)
    response.status = status
    client.place_market_order.return_value = response
    result = await adapter.reconcile(1.0, 50000.0)
    assert result["status"] == "failed"
    assert result["error_type"] == "MutationOutcomeUnknown"
    assert adapter._ensure_reservation_ledger().snapshot()["USDT"]["reserved"] == D("50050")
    assert next(iter(adapter._mutation_outbox.latest_by_key().values()))["status"] == "ambiguous"
    client.place_market_order.reset_mock()
    again = await adapter.reconcile(1.0, 50000.0)
    assert again["status"] == "failed"
    client.place_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_rejection_releases_reservation(config, client):
    adapter = _adapter(config, client)
    adapter._get_net_position = MagicMock(return_value=D("0"))
    response = _order("rejected", OrderSide.BUY)
    response.status = OrderStatus.REJECTED
    client.place_market_order.return_value = response
    result = await adapter.reconcile(1.0, 50000.0)
    assert result["status"] == "failed"
    assert adapter._ensure_reservation_ledger().snapshot()["USDT"]["reserved"] == D("0")
    assert next(iter(adapter._mutation_outbox.latest_by_key().values()))["status"] == "terminal"


@pytest.mark.asyncio
async def test_ambiguous_outcome_holds_reservation(config, client):
    adapter = _adapter(config, client, max_attempts=1)
    adapter._get_net_position = MagicMock(return_value=D("0.0"))

    def _unknown(*_a, **_k):
        raise TimeoutError("socket died mid-flight")

    client.place_market_order.side_effect = _unknown

    res = await adapter.reconcile(1.0, 50000.0)
    assert res["status"] == "failed"

    snap = adapter._ensure_reservation_ledger().snapshot()["USDT"]
    # The order may exist venue-side: funds stay locked.
    assert snap["reserved"] == D("50050")
    assert snap["available"] == D("49950")
    assert adapter._coordinator.ambiguous_keys()


@pytest.mark.asyncio
async def test_ambiguous_outcome_with_default_retry_budget_never_releases(config, client):
    adapter = _adapter(config, client, max_attempts=3)
    adapter._get_net_position = MagicMock(return_value=D("0"))
    client.place_market_order.side_effect = TimeoutError("read timeout after submit")

    result = await adapter.reconcile(1.0, 50000.0)
    assert result["status"] == "failed"
    assert result["error_type"] == "MutationOutcomeUnknown"
    client.place_market_order.assert_called_once()
    snap = adapter._ensure_reservation_ledger().snapshot()["USDT"]
    assert snap["reserved"] == D("50050")
    assert snap["committed"] == D("0")
    entries = adapter._mutation_outbox.latest_by_key()
    assert len(entries) == 1
    assert next(iter(entries.values()))["status"] == "ambiguous"


@pytest.mark.asyncio
async def test_prior_unresolved_order_blocks_new_trading_after_restart(config, client, tmp_path):
    outbox = MutationOutbox(path=tmp_path / "resume-outbox.jsonl")
    outbox.record_pending(
        idempotency_key="earlier-order", proposal_id="previous-proposal",
        operation="spot.market", payload_sha256="previous-hash",
    )
    adapter = _adapter(config, client, extra={"mutation_outbox": outbox})
    adapter._get_net_position = MagicMock(return_value=D("0"))

    result = await adapter.reconcile(1.0, 50000.0)
    assert result["status"] == "failed"
    assert result["error_type"] == "PermissionError"
    client.place_market_order.assert_not_called()
    # Pending-intent evidence remains available for external reconciliation.
    assert [e["idempotency_key"] for e in outbox.latest_by_key().values()] == ["earlier-order"]


@pytest.mark.asyncio
async def test_pending_outbox_write_failure_releases_without_submitting(config, client, tmp_path):
    outbox = MutationOutbox(path=tmp_path / "nonexistent" / "outbox.jsonl")
    adapter = _adapter(config, client, extra={"mutation_outbox": outbox})
    adapter._get_net_position = MagicMock(return_value=D("0"))
    with patch.object(outbox, "record_pending", side_effect=OSError("no disk space")):
        result = await adapter.reconcile(1.0, 50000.0)
    assert result["status"] == "failed"
    client.place_market_order.assert_not_called()
    assert adapter._ensure_reservation_ledger().snapshot()["USDT"]["reserved"] == D("0")


@pytest.mark.asyncio
async def test_short_open_reserves_base(config, client):
    adapter = _adapter(config, client)
    adapter._get_net_position = MagicMock(return_value=D("0.0"))
    client.create_short_sell.return_value = _order("s1", OrderSide.SELL)

    res = await adapter.reconcile(-0.5, 50000.0)
    assert res["status"] == "executed"

    snap = adapter._ensure_reservation_ledger().snapshot()["BTC"]
    assert snap["committed"] == D("0.5")
    assert snap["available"] == D("4.5")


@pytest.mark.asyncio
async def test_second_reconcile_cannot_double_commit(config, client):
    """Funds locked by an in-flight order are unavailable to the next one."""
    adapter = _adapter(config, client, max_attempts=1)
    adapter._get_net_position = MagicMock(return_value=D("0.0"))

    def _unknown(*_a, **_k):
        raise TimeoutError("ambiguous")

    client.place_market_order.side_effect = _unknown
    first = await adapter.reconcile(1.0, 50000.0)
    assert first["status"] == "failed"

    # Outcome now provably fine venue-side, but the hold is still local.
    client.place_market_order.side_effect = None
    client.place_market_order.return_value = _order("o2", OrderSide.BUY)
    second = await adapter.reconcile(2.0, 50000.0)

    # Even a smaller order must be blocked while the first is unresolved.
    assert second["status"] == "failed"
    assert second["error_type"] == "PermissionError"
    snap = adapter._ensure_reservation_ledger().snapshot()["USDT"]
    assert snap["reserved"] >= D("50050")


def test_broker_blocked_funds_are_not_available_for_new_order(config, client):
    """A venue hold cannot be re-spent by the local reservation ledger."""
    adapter = _adapter(config, client)
    client.get_balance.return_value = [
        Balance(symbol="USDT", balance=D("100"), blocked=D("900")),
        Balance(symbol="BTC", balance=D("1"), blocked=D("0")),
    ]
    ledger = adapter._ensure_reservation_ledger()
    assert ledger.available_balance("USDT") == D("100")
    from trading_gym.treasury.reservations import ReservationError
    with pytest.raises(ReservationError, match="insufficient"):
        ledger.reserve(order_id="over", currency="USDT", amount=D("200"))
    assert ledger.available_balance("USDT") == D("100")


@pytest.mark.asyncio
async def test_commit_bookkeeping_failure_never_reports_order_success(config, client):
    from trading_gym.treasury.reservations import ReservationLedger
    adapter = _adapter(config, client)
    adapter._get_net_position = MagicMock(return_value=D("0"))
    client.place_market_order.return_value = _order("venue-accepted", OrderSide.BUY)
    with patch.object(ReservationLedger, "commit_settlement", side_effect=OSError("disk full")):
        result = await adapter.reconcile(1.0, 50000.0)
    assert result["status"] == "failed"
    assert result["error_type"] == "MutationOutcomeUnknown"
    assert adapter._ensure_reservation_ledger().snapshot()["USDT"]["reserved"] > 0
    assert next(iter(adapter._mutation_outbox.latest_by_key().values()))["status"] == "ambiguous"
    client.place_market_order.assert_called_once()


@pytest.mark.asyncio
async def test_committed_outcome_journal_failure_retains_pending_barrier(config, client):
    outbox = MutationOutbox()
    adapter = _adapter(config, client, extra={"mutation_outbox": outbox})
    adapter._get_net_position = MagicMock(return_value=D("0"))
    client.place_market_order.return_value = _order("venue-filled", OrderSide.BUY)
    with patch.object(outbox, "record_outcome", side_effect=OSError("disk full")):
        result = await adapter.reconcile(1.0, 50000.0)
    assert result["status"] == "failed"
    assert result["error_type"] == "MutationOutcomeUnknown"
    assert next(iter(outbox.latest_by_key().values()))["status"] == "pending"
    assert client.place_market_order.call_count == 1
