import pytest
from unittest.mock import patch
from trading_gym.market.adapters.enhanced_aban_client import EnhancedAbanClient, OrderSide


@pytest.fixture
def mock_client():
    with (
        patch("trading_gym.market.adapters.enhanced_aban_client.AbantetherClient"),
        patch(
            "trading_gym.market.adapters.enhanced_aban_client.EnhancedAbanClient._make_request"
        ) as mock_req,
    ):
        client = EnhancedAbanClient(api_key="test_key")
        client._make_request = mock_req
        yield client, mock_req


def test_place_limit_order(mock_client):
    client, mock_req = mock_client
    mock_req.return_value = {
        "success": True,
        "data": {
            "id": "123",
            "code": "123",
            "base_symbol": "BTC",
            "quote_symbol": "USDT",
            "side": "buy",
            "type": "limit",
            "status": "open",
            "volume": {"base": "0.1", "quote": "5000.0"},
            "filled_volume": {"base": "0.0", "quote": "0.0"},
            "created_at": "2024-01-01T00:00:00Z",
            "price": "49990.0",
        },
    }

    resp = client.place_limit_order(
        base_symbol="BTC",
        quote_symbol="USDT",
        volume=0.1,
        price=50000.0,
        side=OrderSide.BUY,
    )
    assert resp.id == "123"
    assert resp.side == "buy"


def test_cancel_order(mock_client):
    client, mock_req = mock_client
    mock_req.return_value = {"success": True, "message": "Cancelled"}
    resp = client.cancel_order("123")
    assert resp["success"] is True


def test_get_ticker(mock_client):
    client, mock_req = mock_client
    mock_req.return_value = {
        "success": True,
        "data": {
            "markets": {
                "BTC": {
                    "symbol": "BTC",
                    "last": "49990.0",
                    "high": "51000.0",
                    "low": "49000.0",
                    "vol": "100.0",
                    "change": "2.0",
                    "buy_price": "49990.0",
                    "sell_price": "50010.0",
                    "buy_max": "10.0",
                    "sell_max": "10.0",
                    "active": True,
                }
            }
        },
    }

    ticker = client.get_ticker("BTC")
    assert str(ticker.buy_price) == "49990.0"


def test_get_klines_tv_format(mock_client):
    client, mock_req = mock_client
    mock_req.return_value = {
        "s": "ok",
        "t": [1600000000, 1600000300],
        "o": [100.0, 101.0],
        "h": [102.0, 103.0],
        "l": [99.0, 100.0],
        "c": [101.0, 102.0],
        "v": [10.0, 20.0],
    }
    klines = client.get_klines("BTC", "5m")
    assert len(klines) == 2
    assert klines[0] == [1600000000, 100.0, 102.0, 99.0, 101.0, 10.0]


def test_get_klines_list_format(mock_client):
    client, mock_req = mock_client
    mock_req.return_value = {
        "data": [
            [1600000000, 100.0, 102.0, 99.0, 101.0, 10.0],
            [1600000300, 101.0, 103.0, 100.0, 102.0, 20.0],
        ]
    }
    klines = client.get_klines("BTC", "5m")
    assert len(klines) == 2
    assert klines[0][1] == 100.0


def test_create_short_sell(mock_client):
    client, mock_req = mock_client
    mock_req.return_value = {
        "success": True,
        "data": {
            "id": "short123",
            "code": "short123",
            "base_symbol": "BTC",
            "quote_symbol": "USDT",
            "side": "sell",
            "type": "limit",
            "status": "open",
            "volume": {"base": "0.1", "quote": "5000.0"},
            "filled_volume": {"base": "0.0", "quote": "0.0"},
            "created_at": "2024-01-01T00:00:00Z",
            "price": "49990.0",
            "leverage": 2,
        },
    }

    resp = client.create_short_sell(
        base_symbol="BTC", quote_symbol="USDT", amount=0.1, leverage=2
    )
    assert resp.id == "short123"
    assert resp.side == "sell"


def test_repay_credit_error(mock_client):
    client, mock_req = mock_client
    mock_req.return_value = {"success": False, "error": "Insufficient balance"}

    with pytest.raises(RuntimeError) as exc:
        client.repay_credit(100.0)
    assert "Insufficient balance" in str(exc.value)
