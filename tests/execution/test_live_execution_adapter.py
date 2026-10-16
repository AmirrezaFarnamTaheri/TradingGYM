import asyncio
import pytest
from unittest.mock import MagicMock, patch
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from trading_gym.execution.live_execution_adapter import (
    DryRunLiveExecutionAdapter,
    LiveExecutionAdapter,
)
from trading_gym.production.mutation_retry import MutationOutbox, MutationOutcomeUnknown
from axiomforge.live_trading import LiveConsent
from trading_gym.adapters.enhanced_aban_client import (
    EnhancedAbanClient,
    OrderSide,
    TickerData,
    OrderResponse,
    OrderStatus,
    Balance,
)


@pytest.fixture(autouse=True)
def isolated_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.env.symbol = "BTCUSDT"
    cfg.env.quote_ccy = "USDT"
    cfg.env.decimals.qty_for.return_value = 6
    cfg.env.decimals.price_for.return_value = 2
    cfg.env.decimals.min_qty_for.return_value = 0.001
    cfg.env.cost.taker_fee_bps = 10.0
    return cfg


@pytest.fixture
def mock_client():
    client = MagicMock(spec=EnhancedAbanClient)
    # Funded ledger: spending mutations reserve against live balances.
    client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100000"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("5"), blocked=Decimal("0")),
    ]
    client.get_ticker.return_value = {}
    client.get_short_positions.return_value = []
    return client


@pytest.fixture
def adapter(mock_config, mock_client):
    with patch(
        "trading_gym.execution.live_execution_adapter.EnhancedAbanClient",
        return_value=mock_client,
    ):
        return LiveExecutionAdapter(
            mock_config,
            "fake_token",
            live_consent=LiveConsent(
                actor="test-executor",
                scope="live.execute",
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                nonce="test-session",
            ),
            proposal_id="test-proposal",
        )


@pytest.mark.asyncio
async def test_reconcile_buy_spot(adapter, mock_client):
    # Setup: Current pos 0, Target 1.0 -> Buy 1.0
    adapter._get_net_position = MagicMock(return_value=Decimal("0.0"))
    mock_client.place_market_order.return_value = OrderResponse(
        id="order1",
        code="200",
        base_symbol="BTC",
        quote_symbol="USDT",
        side=OrderSide.BUY,
        type="market",
        status=OrderStatus.FILLED,
        volume={"USDT": Decimal("100")},
        filled_volume={"BTC": Decimal("1.0")},
        created_at="now",
    )

    res = await adapter.reconcile(1.0, 50000.0)

    assert res["status"] == "executed"
    assert res["side"] == "buy"
    assert res["qty"] == 1.0
    mock_client.place_market_order.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_close_short_and_flip(adapter, mock_client):
    # Setup: Current pos -0.5 (Short), Target 1.0 -> Close Short 0.5 + Buy Spot 1.0
    adapter._get_net_position = MagicMock(return_value=Decimal("-0.5"))

    mock_client.get_short_positions.return_value = [
        {"id": "short1", "symbol": "BTCUSDT", "amount": 0.5}
    ]

    # Mock responses: the short close must have its own confirmed fill.
    mock_client.close_short_position.return_value = OrderResponse(
        id="short_close",
        code="200",
        base_symbol="BTC",
        quote_symbol="USDT",
        side=OrderSide.BUY,
        type="market",
        status=OrderStatus.FILLED,
        volume={"USDT": Decimal("25000")},
        filled_volume={"BTC": Decimal("0.5")},
        created_at="now",
    )
    mock_client.place_market_order.return_value = OrderResponse(
        id="order_spot",
        code="200",
        base_symbol="BTC",
        quote_symbol="USDT",
        side=OrderSide.BUY,
        type="market",
        status=OrderStatus.FILLED,
        volume={"USDT": Decimal("100")},
        filled_volume={"BTC": Decimal("1.0")},
        created_at="now",
    )

    await adapter.reconcile(1.0, 50000.0)

    # Should close short
    mock_client.close_short_position.assert_called_with("short1", Decimal("0.5"))
    # And buy remaining 1.0
    mock_client.place_market_order.assert_called_with(
        OrderSide.BUY, "BTC", "USDT", Decimal("1.0")
    )


@pytest.mark.asyncio
async def test_reconcile_sell_short(adapter, mock_client):
    # Setup: Current pos 0, Target -0.5 -> Short Sell 0.5
    adapter._get_net_position = MagicMock(return_value=Decimal("0.0"))

    # Assume BTC is shortable
    with patch(
        "trading_gym.execution.live_execution_adapter.is_shortable", return_value=True
    ):
        mock_client.create_short_sell.return_value = OrderResponse(
            id="short_order",
            code="200",
            base_symbol="BTC",
            quote_symbol="USDT",
            side=OrderSide.SELL,
            type="market",
            status=OrderStatus.FILLED,
            volume={"BTC": Decimal("0.5")},
            filled_volume={"BTC": Decimal("0.5")},
            created_at="now",
        )

        res = await adapter.reconcile(-0.5, 50000.0)

        assert res["status"] == "executed"
        mock_client.create_short_sell.assert_called_with("BTC", "USDT", Decimal("0.5"))


@pytest.mark.asyncio
async def test_flatten(adapter, mock_client):
    adapter._get_net_position = MagicMock(return_value=Decimal("1.5"))
    mock_client.get_orders.return_value = {
        "total": 1,
        "data": [{"id": "open1", "base_symbol": "BTC", "quote_symbol": "USDT"}],
    }
    mock_client.cancel_order.return_value = OrderResponse(
        id="open1",
        code="200",
        base_symbol="BTC",
        quote_symbol="USDT",
        side=OrderSide.SELL,
        type="market",
        status=OrderStatus.CANCELED,
        volume={"BTC": Decimal("0")},
        filled_volume={"BTC": Decimal("0")},
        created_at="now",
    )
    mock_client.place_market_order.return_value = OrderResponse(
        id="close_spot",
        code="200",
        base_symbol="BTC",
        quote_symbol="USDT",
        side=OrderSide.SELL,
        type="market",
        status=OrderStatus.FILLED,
        volume={"BTC": Decimal("1.5")},
        filled_volume={"BTC": Decimal("1.5")},
        created_at="now",
    )

    await adapter.flatten()

    mock_client.cancel_order.assert_called_with("open1")
    mock_client.place_market_order.assert_called_with(
        OrderSide.SELL, "BTC", "USDT", Decimal("1.5")
    )



@pytest.mark.parametrize(
    "response",
    [
        {"total": 2, "data": [{"id": "visible-only"}]},
        {"total": 0, "data": [{"id": "unexpected"}]},
        {"data": [{"id": "visible-only"}]},
        {"total": 1, "data": None},
    ],
)
@pytest.mark.asyncio
async def test_flatten_refuses_incomplete_broker_order_pages_before_mutation(
    adapter, mock_client, response
):
    adapter._get_net_position = MagicMock(return_value=Decimal("1"))
    mock_client.get_orders.return_value = response

    assert await adapter.flatten() is False
    assert adapter._last_flatten_error
    mock_client.cancel_order.assert_not_called()
    mock_client.place_market_order.assert_not_called()
    mock_client.create_short_sell.assert_not_called()
    mock_client.close_short_position.assert_not_called()
    adapter._get_net_position.assert_not_called()


def test_get_live_equity(adapter, mock_client):
    # Mock balances: 100 USDT + 1 BTC
    # BTC Price = 50000
    # Total Equity = 100 + 50000 = 50100

    mock_client.get_balance.return_value = [
        MagicMock(
            symbol="USDT", balance=Decimal("50"), blocked=Decimal("50")
        ),  # 100 USDT
        MagicMock(symbol="BTC", balance=Decimal("1"), blocked=Decimal("0")),  # 1 BTC
    ]

    mock_client.get_ticker.return_value = {
        "BTCUSDT": TickerData(
            symbol="BTCUSDT",
            buy_price=Decimal("49900"),
            sell_price=Decimal("50100"),
            buy_max=Decimal("0"),
            sell_max=Decimal("0"),
            active=True,
        )
    }

    equity = adapter.get_live_equity()
    assert equity == 50100.0


def test_unpriced_non_quote_asset_cannot_be_silently_omitted(adapter, mock_client):
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("1"), blocked=Decimal("0")),
    ]
    mock_client.get_ticker.return_value = {}
    with pytest.raises(RuntimeError, match="unable to determine live equity"):
        adapter.get_live_equity()


def test_quote_reservation_rounds_up_and_rejects_invalid_prices(adapter):
    cost = adapter._quote_cost(Decimal("1.000000001"), 1.0)
    assert cost >= Decimal("1.000000001") * Decimal("1.001")
    assert cost == Decimal("1.00100001")
    for price in (float("nan"), float("inf"), 0, -1):
        with pytest.raises(ValueError, match="invalid_quote_reservation_inputs"):
            adapter._quote_cost(Decimal("1"), price)


def test_missing_or_invalid_fee_configuration_fails_closed(adapter):
    adapter.cfg.env.cost.taker_fee_bps = float("nan")
    with pytest.raises(ValueError, match="invalid_taker_fee_configuration"):
        adapter._quote_cost(Decimal("1"), 10.0)


def test_get_live_equity_fails_closed_when_no_positive_equity(adapter, mock_client):
    mock_client.get_balance.return_value = []
    mock_client.get_ticker.return_value = {}
    mock_client.get_short_positions.return_value = []

    with pytest.raises(RuntimeError, match="unable to determine live equity"):
        adapter.get_live_equity()


@pytest.mark.asyncio
async def test_dry_run_adapter_never_submits_or_cancels_orders(
    mock_config, mock_client
):
    with patch(
        "trading_gym.execution.live_execution_adapter.EnhancedAbanClient",
        return_value=mock_client,
    ):
        dry_run = DryRunLiveExecutionAdapter(mock_config, "fake_token")

    assert dry_run.can_acknowledge_panic is False
    dry_run._get_net_position = MagicMock(return_value=Decimal("0.25"))
    result = await dry_run.reconcile(1.0, 50000.0)
    assert result["status"] == "simulated"
    assert result["current_position"] == 0.25
    # This test fixture still has 5 BTC in the broker account and no
    # independently verified open-order snapshot. A dry run cannot flatten
    # funds, and must not report that a panic has been safely resolved.
    assert await dry_run.flatten() is False
    assert dry_run._last_flatten_error

    mock_client.place_market_order.assert_not_called()
    mock_client.create_short_sell.assert_not_called()
    mock_client.close_short_position.assert_not_called()
    mock_client.cancel_order.assert_not_called()


def test_get_net_position_long(adapter, mock_client):
    # Setup: Spot 0.5, Short 0.0
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("1000.0"), blocked=Decimal("0.0")),
        Balance(symbol="BTC", balance=Decimal("0.5"), blocked=Decimal("0.0")),
    ]
    mock_client.get_short_positions.return_value = []

    pos = adapter._get_net_position()
    assert pos == Decimal("0.5")


def test_get_net_position_short(adapter, mock_client):
    # Setup: Spot 0.0, Short 1.0
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("50000.0"), blocked=Decimal("0.0")),
        Balance(symbol="BTC", balance=Decimal("0.0"), blocked=Decimal("0.0")),
    ]
    mock_client.get_short_positions.return_value = [
        {"symbol": "BTCUSDT", "amount": 1.0}
    ]

    pos = adapter._get_net_position()
    assert pos == Decimal("-1.0")


@pytest.mark.asyncio
async def test_reconcile_dust(adapter, mock_client):
    # Current: 0.5
    # Target: 0.5000001
    adapter._get_net_position = MagicMock(return_value=Decimal("0.5"))

    res = await adapter.reconcile(0.5000001, 50000.0)

    assert res["status"] == "skipped"
    assert res["reason"] == "dust"

    mock_client.place_market_order.assert_not_called()
@pytest.mark.asyncio
async def test_flatten_net_zero_does_not_mask_offsetting_spot_and_shorts(
    adapter, mock_client
):
    """A zero net is not a flat portfolio if offsetting gross legs remain."""
    adapter._get_net_position = MagicMock(return_value=Decimal("0"))
    mock_client.get_orders.return_value = {"data": [], "total": 0}
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("1000"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("1"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = [
        {"id": "hedged-short", "symbol": "BTCUSDT", "amount": "1"}
    ]
    assert await adapter.flatten() is False
    assert "gross_exposure" in adapter._last_flatten_error
    mock_client.place_market_order.assert_not_called()
    mock_client.close_short_position.assert_not_called()


@pytest.mark.asyncio
async def test_flatten_requires_verified_post_order_position(adapter, mock_client):
    """A successful venue fill does not itself prove an emergency is complete."""
    adapter._get_net_position = MagicMock(return_value=Decimal("1"))
    mock_client.get_orders.return_value = {"data": [], "total": 0}
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("1000"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("1"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.place_market_order.return_value = OrderResponse(
        id="filled", code="200", base_symbol="BTC", quote_symbol="USDT",
        side=OrderSide.SELL, type="market", status=OrderStatus.FILLED,
        volume={"BTC": Decimal("1")},
        filled_volume={"BTC": Decimal("1")},
        created_at="now",
    )
    assert await adapter.flatten() is False
    assert "gross_exposure" in adapter._last_flatten_error
    mock_client.place_market_order.assert_called_once()


@pytest.mark.asyncio
async def test_flatten_succeeds_when_broker_confirms_no_gross_exposure(
    adapter, mock_client
):
    adapter._get_net_position = MagicMock(return_value=Decimal("1"))
    mock_client.get_orders.return_value = {"data": [], "total": 0}
    mock_client.get_balance.side_effect = [
        [
            Balance(symbol="USDT", balance=Decimal("1000"), blocked=Decimal("0")),
            Balance(symbol="BTC", balance=Decimal("1"), blocked=Decimal("0")),
        ],
        [
            Balance(symbol="USDT", balance=Decimal("1000"), blocked=Decimal("0")),
            Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
        ],
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.place_market_order.return_value = OrderResponse(
        id="filled", code="200", base_symbol="BTC", quote_symbol="USDT",
        side=OrderSide.SELL, type="market", status=OrderStatus.FILLED,
        volume={"BTC": Decimal("1")},
        filled_volume={"BTC": Decimal("1")},
        created_at="now",
    )
    assert await adapter.flatten() is True
    assert adapter._last_flatten_error == ""
    # A closing fill is only resolved after the snapshot confirms flat.
    assert [
        row["status"] for row in adapter._mutation_outbox.latest_by_key().values()
    ] == ["committed"]


@pytest.mark.asyncio
async def test_reconcile_does_not_buy_spot_when_short_inventory_missing(
    adapter, mock_client
):
    adapter._get_net_position = MagicMock(return_value=Decimal("-0.5"))
    mock_client.get_short_positions.return_value = []
    result = await adapter.reconcile(1.0, 50000.0)
    assert result["status"] == "failed"
    mock_client.close_short_position.assert_not_called()
    mock_client.place_market_order.assert_not_called()


@pytest.mark.parametrize("price", [float("nan"), float("inf"), 0.0, -5.0])
@pytest.mark.asyncio
async def test_reconcile_rejects_invalid_price_even_for_sell_and_short(
    adapter, mock_client, price
):
    adapter._get_net_position = MagicMock(return_value=Decimal("0"))
    with patch(
        "trading_gym.execution.live_execution_adapter.is_shortable", return_value=True
    ):
        result = await adapter.reconcile(-0.5, price)
    assert result["status"] == "failed"
    mock_client.create_short_sell.assert_not_called()


@pytest.mark.parametrize(
    "shorts",
    [
        [{"id": "s1", "symbol": "BTCUSDT", "amount": "0.1"}],
        [{"id": "s1", "symbol": "BTCUSDT", "amount": "NaN"}],
        [{"id": "s1", "symbol": "BTCUSDT", "amount": "-1"}],
        [
            {"id": "s1", "symbol": "BTCUSDT", "amount": "0.5"},
            {"id": "s1", "symbol": "BTCUSDT", "amount": "0.5"},
        ],
    ],
)
@pytest.mark.asyncio
async def test_reconcile_rejects_unreconcilable_short_inventory(
    adapter, mock_client, shorts
):
    adapter._get_net_position = MagicMock(return_value=Decimal("-0.5"))
    mock_client.get_short_positions.return_value = shorts
    result = await adapter.reconcile(1.0, 50000.0)
    assert result["status"] == "failed"
    mock_client.close_short_position.assert_not_called()
    mock_client.place_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_unverified_flatten_keeps_durable_reconciliation_hold_after_restart(
    adapter, mock_client
):
    """A filled close with stale venue balances must not replay on restart."""
    adapter._get_net_position = MagicMock(return_value=Decimal("1"))
    mock_client.get_orders.return_value = {"data": [], "total": 0}
    mock_client.get_short_positions.return_value = []
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("1000"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("1"), blocked=Decimal("0")),
    ]
    mock_client.place_market_order.return_value = OrderResponse(
        id="first-fill",
        code="200",
        base_symbol="BTC",
        quote_symbol="USDT",
        side=OrderSide.SELL,
        type="market",
        status=OrderStatus.FILLED,
        volume={"BTC": Decimal("1")},
        filled_volume={"BTC": Decimal("1")},
        created_at="now",
    )
    assert await adapter.flatten() is False
    entries = adapter._mutation_outbox.latest_by_key()
    assert len(entries) == 1
    assert next(iter(entries.values()))["status"] == "ambiguous"

    # Construct a fresh journal object, simulating a worker restart.
    with patch(
        "trading_gym.execution.live_execution_adapter.EnhancedAbanClient",
        return_value=mock_client,
    ):
        fresh = LiveExecutionAdapter(
            adapter.cfg,
            "fake_token",
            live_consent=adapter._live_consent,
            proposal_id=adapter._proposal_id,
            mutation_outbox=MutationOutbox(adapter._mutation_outbox.path),
        )
    fresh._get_net_position = MagicMock(return_value=Decimal("1"))
    assert await fresh.flatten() is False
    mock_client.place_market_order.assert_called_once()


@pytest.mark.asyncio
async def test_long_to_short_reversal_rejects_forbidden_short_before_any_sell(
    adapter, mock_client
):
    adapter._get_net_position = MagicMock(return_value=Decimal("0.5"))
    with patch(
        "trading_gym.execution.live_execution_adapter.is_shortable",
        return_value=False,
    ):
        result = await adapter.reconcile(-0.2, 50000.0)
    assert result == {"status": "skipped", "reason": "short_not_allowed"}
    mock_client.place_market_order.assert_not_called()
    mock_client.create_short_sell.assert_not_called()


@pytest.mark.asyncio
async def test_spot_sell_exactly_at_min_qty_is_an_actual_order(adapter, mock_client):
    adapter._get_net_position = MagicMock(return_value=Decimal("0.001"))
    mock_client.place_market_order.return_value = OrderResponse(
        id="exact-min-spot-sale",
        code="200",
        base_symbol="BTC",
        quote_symbol="USDT",
        side=OrderSide.SELL,
        type="market",
        status=OrderStatus.FILLED,
        volume={"BTC": Decimal("0.001")},
        filled_volume={"BTC": Decimal("0.001")},
        created_at="now",
    )
    result = await adapter.reconcile(0.0, 50000.0)
    assert result["status"] == "executed"
    assert result["order_id"] == "exact-min-spot-sale"
    mock_client.place_market_order.assert_called_once_with(
        OrderSide.SELL, "BTC", "USDT", Decimal("0.001")
    )


@pytest.mark.asyncio
async def test_split_into_subminimum_legs_is_not_misreported_as_executed(
    adapter, mock_client
):
    adapter._get_net_position = MagicMock(return_value=Decimal("0.0006"))
    with patch(
        "trading_gym.execution.live_execution_adapter.is_shortable",
        return_value=True,
    ):
        result = await adapter.reconcile(-0.0006, 50000.0)
    assert result == {"status": "skipped", "reason": "no_executable_leg"}
    mock_client.place_market_order.assert_not_called()
    mock_client.create_short_sell.assert_not_called()


@pytest.mark.asyncio
async def test_emergency_flatten_waits_for_inflight_reconciliation(
    adapter, mock_client
):
    adapter._get_net_position = MagicMock(return_value=Decimal("0"))
    mock_client.get_orders.return_value = {"data": [], "total": 0}
    mock_client.get_short_positions.return_value = []
    mock_client.get_balance.return_value = [
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
        Balance(symbol="USDT", balance=Decimal("500"), blocked=Decimal("0")),
    ]
    await adapter._reconcile_lock.acquire()
    emergency = asyncio.create_task(adapter.flatten())
    await asyncio.sleep(0)
    assert not emergency.done()
    mock_client.get_orders.assert_not_called()
    adapter._reconcile_lock.release()
    assert await emergency is True
    mock_client.get_orders.assert_called()
    mock_client.place_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_untyped_cancel_ack_is_unverified_until_order_detail_confirms(
    adapter, mock_client
):
    mock_client.cancel_order.return_value = {"message": "accepted"}
    mock_client.get_order_details.return_value = OrderResponse(
        id="pending-order",
        code="200",
        base_symbol="BTC",
        quote_symbol="USDT",
        side=OrderSide.SELL,
        type="market",
        status=OrderStatus.OPEN,
        volume={"BTC": Decimal("1")},
        filled_volume={"BTC": Decimal("0")},
        created_at="now",
    )
    with pytest.raises(MutationOutcomeUnknown, match="cancel_not_verified"):
        await adapter._mutate(
            "order.cancel",
            side="none",
            quantity=Decimal("0"),
            reference_id="pending-order",
        )
    mock_client.cancel_order.assert_called_once_with("pending-order")
    mock_client.get_order_details.assert_called_once_with("pending-order")
    assert next(iter(adapter._mutation_outbox.latest_by_key().values()))[
        "status"
    ] == "ambiguous"


@pytest.mark.asyncio
async def test_cancel_dict_ack_commits_only_after_confirmed_terminal_order(
    adapter, mock_client
):
    mock_client.cancel_order.return_value = {"message": "accepted"}
    mock_client.get_order_details.return_value = OrderResponse(
        id="pending-order",
        code="200",
        base_symbol="BTC",
        quote_symbol="USDT",
        side=OrderSide.SELL,
        type="market",
        status=OrderStatus.CANCELED,
        volume={"BTC": Decimal("1")},
        filled_volume={"BTC": Decimal("0")},
        created_at="now",
    )
    result = await adapter._mutate(
        "order.cancel",
        side="none",
        quantity=Decimal("0"),
        reference_id="pending-order",
    )
    assert result == {"message": "accepted"}
    assert next(iter(adapter._mutation_outbox.latest_by_key().values()))[
        "status"
    ] == "committed"


@pytest.mark.asyncio
async def test_cancel_confirmation_failure_preserves_unknown_order_state(
    adapter, mock_client
):
    mock_client.cancel_order.return_value = {"message": "accepted"}
    mock_client.get_order_details.side_effect = ConnectionError("venue offline")
    with pytest.raises(MutationOutcomeUnknown, match="cancel_confirmation_unavailable"):
        await adapter._mutate(
            "order.cancel",
            side="none",
            quantity=Decimal("0"),
            reference_id="pending-order",
        )
    assert next(iter(adapter._mutation_outbox.latest_by_key().values()))[
        "status"
    ] == "ambiguous"


@pytest.mark.asyncio
async def test_dict_short_close_ack_requires_broker_position_disappearance(
    adapter, mock_client
):
    mock_client.close_short_position.return_value = {"success": True}
    mock_client.get_short_positions.return_value = [
        {"id": "short-1", "symbol": "BTCUSDT", "amount": "0.5"}
    ]
    with pytest.raises(MutationOutcomeUnknown, match="short_close_not_verified"):
        await adapter._mutate(
            "short.close",
            side="buy",
            quantity=Decimal("0"),
            reference_id="short-1",
        )
    assert next(iter(adapter._mutation_outbox.latest_by_key().values()))[
        "status"
    ] == "ambiguous"


@pytest.mark.asyncio
async def test_full_short_close_uses_broker_readback_not_generic_ack(
    adapter, mock_client
):
    mock_client.close_short_position.return_value = {"success": True}
    mock_client.get_short_positions.return_value = []
    result = await adapter._mutate(
        "short.close",
        side="buy",
        quantity=Decimal("0"),
        reference_id="short-1",
    )
    assert result == {"success": True}
    mock_client.get_short_positions.assert_called_once()
    assert next(iter(adapter._mutation_outbox.latest_by_key().values()))[
        "status"
    ] == "committed"


@pytest.mark.asyncio
async def test_partial_short_close_ack_without_fill_proof_remains_unresolved(
    adapter, mock_client
):
    mock_client.close_short_position.return_value = {"success": True}
    with pytest.raises(MutationOutcomeUnknown, match="partial_short_close_unverified"):
        await adapter._mutate(
            "short.close",
            side="buy",
            quantity=Decimal("0.2"),
            reference_id="short-1",
        )
    assert next(iter(adapter._mutation_outbox.latest_by_key().values()))[
        "status"
    ] == "ambiguous"


@pytest.mark.asyncio
async def test_paginated_open_order_count_prevents_false_flat_verification(
    adapter, mock_client
):
    mock_client.get_balance.return_value = [
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.get_orders.return_value = {"total": 11, "data": []}
    with pytest.raises(RuntimeError, match="open_orders_not_cleared"):
        await adapter._verify_broker_flat()


@pytest.mark.parametrize("total", [None, -1, "0", True])
@pytest.mark.asyncio
async def test_invalid_broker_open_order_count_rejected(adapter, mock_client, total):
    mock_client.get_balance.return_value = [
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.get_orders.return_value = {"total": total, "data": []}
    with pytest.raises(RuntimeError, match="invalid_broker_order_count"):
        await adapter._verify_broker_flat()


@pytest.mark.parametrize(
    "available,blocked",
    [
        (Decimal("NaN"), Decimal("0")),
        (Decimal("-0.1"), Decimal("0")),
        (Decimal("1"), Decimal("-0.5")),
        (Decimal("Infinity"), Decimal("0")),
    ],
)
def test_net_position_rejects_invalid_base_balances(
    adapter, mock_client, available, blocked
):
    # Exercise malformed SDK snapshots directly. Pydantic Balance already
    # rejects NaN/Infinity during object construction, before this boundary.
    mock_client.get_balance.return_value = [
        MagicMock(symbol="BTC", balance=available, blocked=blocked)
    ]
    with pytest.raises(ValueError, match="invalid_broker_base_balance"):
        adapter._get_net_position()


def test_net_position_rejects_duplicate_base_balance_rows(adapter, mock_client):
    mock_client.get_balance.return_value = [
        Balance(symbol="BTC", balance=Decimal("1"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("2"), blocked=Decimal("0")),
    ]
    with pytest.raises(ValueError, match="duplicate_broker_base_balance"):
        adapter._get_net_position()


@pytest.mark.parametrize("amount", ["NaN", "-0.1", "Infinity"])
def test_net_position_rejects_invalid_short_inventory(
    adapter, mock_client, amount
):
    mock_client.get_short_positions.return_value = [
        {"symbol": "BTCUSDT", "amount": amount}
    ]
    with pytest.raises(ValueError, match="invalid_broker_short_position"):
        adapter._get_net_position()


def test_net_position_requires_actual_short_snapshot(adapter, mock_client):
    mock_client.get_short_positions.return_value = None
    with pytest.raises(ValueError, match="broker_position_snapshot_unavailable"):
        adapter._get_net_position()


@pytest.mark.asyncio
async def test_broker_flat_rejects_missing_pagination_count(adapter, mock_client):
    mock_client.get_balance.return_value = [
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0"))
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.get_orders.return_value = {"data": []}
    with pytest.raises(RuntimeError, match="invalid_broker_order_count"):
        await adapter._verify_broker_flat()


@pytest.mark.asyncio
async def test_dry_run_flatten_succeeds_only_after_readonly_gross_flat_snapshot(
    mock_config, mock_client
):
    with patch(
        "trading_gym.execution.live_execution_adapter.EnhancedAbanClient",
        return_value=mock_client,
    ):
        dry_run = DryRunLiveExecutionAdapter(mock_config, "fake_token")

    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.get_orders.return_value = {"data": [], "total": 0}

    assert await dry_run.flatten() is True
    assert dry_run._last_flatten_error == ""
    mock_client.get_orders.assert_called_once()
    mock_client.place_market_order.assert_not_called()
    mock_client.cancel_order.assert_not_called()
    mock_client.create_short_sell.assert_not_called()
    mock_client.close_short_position.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_never_acknowledges_offsetting_gross_exposure(
    mock_config, mock_client
):
    with patch(
        "trading_gym.execution.live_execution_adapter.EnhancedAbanClient",
        return_value=mock_client,
    ):
        dry_run = DryRunLiveExecutionAdapter(mock_config, "fake_token")

    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("1"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = [
        {"id": "short-1", "symbol": "BTCUSDT", "amount": "1"}
    ]
    mock_client.get_orders.return_value = {"data": [], "total": 0}
    assert await dry_run.flatten() is False
    assert "gross_exposure_not_cleared" in dry_run._last_flatten_error

    mock_client.place_market_order.assert_not_called()
    mock_client.cancel_order.assert_not_called()
    mock_client.create_short_sell.assert_not_called()
    mock_client.close_short_position.assert_not_called()


@pytest.mark.asyncio
async def test_dry_run_refuses_unverified_open_orders(
    mock_config, mock_client
):
    with patch(
        "trading_gym.execution.live_execution_adapter.EnhancedAbanClient",
        return_value=mock_client,
    ):
        dry_run = DryRunLiveExecutionAdapter(mock_config, "fake_token")

    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.get_orders.return_value = {"data": [], "total": 1}
    assert await dry_run.flatten() is False
    assert "open_orders_not_cleared" in dry_run._last_flatten_error
    mock_client.place_market_order.assert_not_called()
    mock_client.cancel_order.assert_not_called()


def test_only_governed_live_adapter_can_acknowledge_shared_panic(
    adapter, mock_config, mock_client
) -> None:
    assert adapter.can_acknowledge_panic is True

    with patch(
        "trading_gym.execution.live_execution_adapter.EnhancedAbanClient",
        return_value=mock_client,
    ):
        ungoverned_live = LiveExecutionAdapter(mock_config, "fake_token")
        dry_run_with_live_fields = DryRunLiveExecutionAdapter(
            mock_config,
            "fake_token",
            live_consent=adapter._live_consent,
            proposal_id=adapter._proposal_id,
        )
    assert ungoverned_live.can_acknowledge_panic is False
    assert dry_run_with_live_fields.can_acknowledge_panic is False


@pytest.mark.asyncio
async def test_flat_verification_requires_explicit_base_balance_row(
    adapter, mock_client
):
    """Missing BTC balance must not be interpreted as zero funded exposure."""
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0"))
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.get_orders.return_value = {"total": 0, "data": []}
    with pytest.raises(ValueError, match="missing_broker_base_balance"):
        await adapter._verify_broker_flat()


@pytest.mark.asyncio
async def test_flat_verification_rejects_negative_blocked_balance(
    adapter, mock_client
):
    """A negative hold cannot cancel genuine spot exposure in the sum."""
    mock_client.get_balance.return_value = [
        Balance(symbol="BTC", balance=Decimal("1"), blocked=Decimal("-1"))
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.get_orders.return_value = {"total": 0, "data": []}
    with pytest.raises(ValueError, match="invalid_broker_spot_balance"):
        await adapter._verify_broker_flat()


@pytest.mark.asyncio
async def test_flat_verification_rejects_duplicate_base_balance_rows(
    adapter, mock_client
):
    """Duplicate balances make broker position evidence ambiguous."""
    mock_client.get_balance.return_value = [
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.get_orders.return_value = {"total": 0, "data": []}
    with pytest.raises(ValueError, match="duplicate_broker_base_balance"):
        await adapter._verify_broker_flat()


@pytest.mark.asyncio
async def test_flat_verification_accepts_explicit_zero_base_snapshot(
    adapter, mock_client
):
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.get_orders.return_value = {"total": 0, "data": []}
    await adapter._verify_broker_flat()


def test_net_position_does_not_infer_missing_base_balance_as_zero(
    adapter, mock_client
):
    """A truncated balance response cannot become a new trading baseline."""
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0"))
    ]
    mock_client.get_short_positions.return_value = []
    with pytest.raises(ValueError, match="missing_broker_base_balance"):
        adapter._get_net_position()


def test_net_position_accepts_explicit_zero_base_balance(adapter, mock_client):
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = []
    assert adapter._get_net_position() == Decimal("0")


@pytest.mark.asyncio
async def test_panic_global_flat_proof_rejects_other_asset_holdings(
    adapter, mock_client
):
    """A global panic cannot be cleared while an unrelated funded asset remains."""
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
        Balance(symbol="ETH", balance=Decimal("1"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.get_orders.return_value = {"data": [], "total": 0}
    with pytest.raises(RuntimeError, match="gross_exposure_not_cleared"):
        await adapter._verify_broker_flat()


@pytest.mark.asyncio
async def test_panic_global_flat_proof_rejects_unrelated_short(adapter, mock_client):
    """Net-zero BTC alone never proves a globally flat account."""
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = [
        {"id": "eth-short", "symbol": "ETHUSDT", "amount": "0.5"}
    ]
    mock_client.get_orders.return_value = {"data": [], "total": 0}
    with pytest.raises(RuntimeError, match="gross_exposure_not_cleared"):
        await adapter._verify_broker_flat()


@pytest.mark.asyncio
async def test_global_panic_proof_requests_all_open_orders(adapter, mock_client):
    """Pair-filtered order pages cannot prove another pair has no open order."""
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=Decimal("0"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = []
    mock_client.get_orders.return_value = {"data": [], "total": 0}
    await adapter._verify_broker_flat()
    kwargs = mock_client.get_orders.call_args.kwargs
    assert "base_symbol" not in kwargs and "quote_symbol" not in kwargs
    assert set(kwargs["status"]) == {
        OrderStatus.NEW, OrderStatus.OPEN, OrderStatus.PROCESSING,
    }


@pytest.mark.parametrize(
    "available,blocked",
    [
        (Decimal("-1"), Decimal("0")),
        (Decimal("1"), Decimal("-1")),
        (Decimal("NaN"), Decimal("0")),
    ],
)
def test_live_equity_rejects_invalid_balance_components(
    adapter, mock_client, available, blocked
):
    """Malformed broker rows must not be silently omitted as zero exposure."""
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0")),
        Balance(symbol="BTC", balance=available, blocked=blocked),
    ]
    mock_client.get_ticker.return_value = {}
    with pytest.raises(RuntimeError, match="unable to determine live equity"):
        adapter.get_live_equity()


@pytest.mark.parametrize("amount,entry_price", [("-1", "100"), ("0.2", "-5")])
def test_live_equity_rejects_invalid_short_inventory(
    adapter, mock_client, amount, entry_price
):
    mock_client.get_balance.return_value = [
        Balance(symbol="USDT", balance=Decimal("100"), blocked=Decimal("0")),
    ]
    mock_client.get_short_positions.return_value = [
        {"symbol": "BTCUSDT", "amount": amount, "entry_price": entry_price}
    ]
    mock_client.get_ticker.return_value = {
        "BTCUSDT": TickerData(
            symbol="BTCUSDT",
            buy_price=Decimal("99"),
            sell_price=Decimal("101"),
            buy_max=Decimal("0"),
            sell_max=Decimal("0"),
            active=True,
        )
    }
    with pytest.raises(RuntimeError, match="unable to determine live equity"):
        adapter.get_live_equity()


@pytest.mark.parametrize(
    "orders",
    [
        [{"id": "unknown", "base_symbol": "ETH", "quote_symbol": "USDT"}],
        [{"id": "incomplete"}],
        [{"id": "", "base_symbol": "BTC", "quote_symbol": "USDT"}],
        ["not-an-order"],
    ],
)
@pytest.mark.asyncio
async def test_global_panic_preflight_blocks_unowned_orders_before_mutation(
    adapter, mock_client, orders
):
    """A global emergency cannot start a market close with unseen pair orders."""
    adapter._get_net_position = MagicMock(return_value=Decimal("1"))
    mock_client.get_orders.return_value = {"total": len(orders), "data": orders}
    assert await adapter.flatten() is False
    assert "broker_open_order" in adapter._last_flatten_error
    mock_client.cancel_order.assert_not_called()
    mock_client.place_market_order.assert_not_called()
    mock_client.close_short_position.assert_not_called()
    mock_client.create_short_sell.assert_not_called()
    adapter._get_net_position.assert_not_called()
    kwargs = mock_client.get_orders.call_args.kwargs
    assert "base_symbol" not in kwargs
    assert "quote_symbol" not in kwargs
