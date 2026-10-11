import pytest
from unittest.mock import MagicMock, patch
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from trading_gym.execution.live_execution_adapter import (
    DryRunLiveExecutionAdapter,
    LiveExecutionAdapter,
)
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
        id="short_close", code="200", base_symbol="BTC", quote_symbol="USDT",
        side=OrderSide.BUY, type="market", status=OrderStatus.FILLED,
        volume={"USDT": Decimal("25000")},
        filled_volume={"BTC": Decimal("0.5")}, created_at="now",
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
    mock_client.get_orders.return_value = {"data": [{"id": "open1"}]}
    mock_client.cancel_order.return_value = OrderResponse(
        id="open1", code="200", base_symbol="BTC", quote_symbol="USDT",
        side=OrderSide.SELL, type="market", status=OrderStatus.CANCELED,
        volume={"BTC": Decimal("0")}, filled_volume={"BTC": Decimal("0")},
        created_at="now",
    )
    mock_client.place_market_order.return_value = OrderResponse(
        id="close_spot", code="200", base_symbol="BTC", quote_symbol="USDT",
        side=OrderSide.SELL, type="market", status=OrderStatus.FILLED,
        volume={"BTC": Decimal("1.5")}, filled_volume={"BTC": Decimal("1.5")},
        created_at="now",
    )

    await adapter.flatten()

    mock_client.cancel_order.assert_called_with("open1")
    mock_client.place_market_order.assert_called_with(
        OrderSide.SELL, "BTC", "USDT", Decimal("1.5")
    )


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

    dry_run._get_net_position = MagicMock(return_value=Decimal("0.25"))
    result = await dry_run.reconcile(1.0, 50000.0)
    assert result["status"] == "simulated"
    assert result["current_position"] == 0.25
    assert await dry_run.flatten() is True

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
