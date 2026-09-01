"""Tests for WebSocket client."""

from unittest.mock import Mock

import pytest

from trading_gym.adapters.aban_websocket import (
    ChannelType,
    Message,
    WebSocketClient,
    WebSocketConfig,
)


@pytest.fixture
def ws_config():
    """Create test WebSocket configuration."""
    return WebSocketConfig(
        url="ws://localhost:8765/test",
        api_key="test-key",
        reconnect_delay_base=0.1,
        reconnect_delay_max=1.0,
        heartbeat_interval=0.5,
        heartbeat_timeout=2.0,
        message_queue_maxlen=100,
        allow_private_url=True,
    )


@pytest.fixture
def ws_client(ws_config):
    """Create test WebSocket client."""
    return WebSocketClient(ws_config)


def test_subscribe(ws_client):
    """Test subscription to channels."""
    callback = Mock()
    ws_client.subscribe(ChannelType.TICKER, "BTC", callback)

    assert len(ws_client._subscriptions) == 1
    key = "ticker:BTC"
    assert key in ws_client._subscriptions
    assert key in ws_client._queues


def test_unsubscribe(ws_client):
    """Test unsubscription from channels."""
    callback = Mock()
    ws_client.subscribe(ChannelType.TICKER, "BTC", callback)
    ws_client.unsubscribe(ChannelType.TICKER, "BTC")

    assert len(ws_client._subscriptions) == 0


def test_make_key(ws_client):
    """Test key generation for channel + symbol."""
    key1 = ws_client._make_key(ChannelType.TICKER, "BTC")
    key2 = ws_client._make_key(ChannelType.TICKER, None)

    assert key1 == "ticker:BTC"
    assert key2 == "ticker:*"


@pytest.mark.asyncio
async def test_handle_message_with_sequence(ws_client):
    """Test message handling with sequence tracking."""
    callback = Mock()
    ws_client.subscribe(ChannelType.TICKER, "BTC", callback)

    # Handle messages with sequences
    await ws_client._handle_message(
        {
            "channel": "ticker",
            "symbol": "BTC",
            "seq": 0,
            "data": {"price": 50000},
        }
    )

    await ws_client._handle_message(
        {
            "channel": "ticker",
            "symbol": "BTC",
            "seq": 1,
            "data": {"price": 50100},
        }
    )

    # Check queue
    key = "ticker:BTC"
    assert len(ws_client._queues[key]) == 2


@pytest.mark.asyncio
async def test_handle_message_sequence_gap(ws_client, caplog):
    """Test detection of sequence gaps."""
    callback = Mock()
    ws_client.subscribe(ChannelType.TICKER, "BTC", callback)

    # Send message with sequence 0
    await ws_client._handle_message(
        {
            "channel": "ticker",
            "symbol": "BTC",
            "seq": 0,
            "data": {"price": 50000},
        }
    )

    # Skip sequence 1, send 2
    await ws_client._handle_message(
        {
            "channel": "ticker",
            "symbol": "BTC",
            "seq": 2,
            "data": {"price": 50100},
        }
    )

    # Should log warning about sequence gap
    assert "Sequence gap detected" in caplog.text


@pytest.mark.asyncio
async def test_heartbeat_response(ws_client):
    """Test heartbeat response handling."""
    initial_time = ws_client._last_heartbeat_response

    await ws_client._handle_message({"type": "pong"})

    assert ws_client._last_heartbeat_response > initial_time


def test_get_stats(ws_client):
    """Test statistics retrieval."""
    callback = Mock()
    ws_client.subscribe(ChannelType.TICKER, "BTC", callback)

    stats = ws_client.get_stats()

    assert "connected" in stats
    assert "reconnect_count" in stats
    assert stats["subscriptions"] == 1


@pytest.mark.asyncio
async def test_queue_overflow(ws_client):
    """Test queue overflow with message dropping."""
    ws_client.config.message_queue_maxlen = 2
    callback = Mock()
    ws_client.subscribe(ChannelType.TICKER, "BTC", callback)

    # Send 3 messages to overflow queue
    for i in range(3):
        await ws_client._handle_message(
            {
                "channel": "ticker",
                "symbol": "BTC",
                "data": {"price": 50000 + i},
            }
        )

    key = "ticker:BTC"
    # Should only keep 2 messages (oldest dropped)
    assert len(ws_client._queues[key]) == 2


@pytest.mark.asyncio
async def test_dispatch_to_sync_callback(ws_client):
    """Test dispatch to synchronous callback."""
    messages = []

    def callback(msg: Message):
        messages.append(msg)

    ws_client.subscribe(ChannelType.TICKER, "BTC", callback)

    # Add message to queue
    await ws_client._handle_message(
        {
            "channel": "ticker",
            "symbol": "BTC",
            "data": {"price": 50000},
        }
    )

    # Manually trigger one dispatch cycle
    ws_client._running = True
    key = "ticker:BTC"
    if key in ws_client._subscriptions and ws_client._queues[key]:
        _, _, cb = ws_client._subscriptions[key]
        msg = ws_client._queues[key].popleft()
        cb(msg)

    assert len(messages) == 1
    assert messages[0].channel == ChannelType.TICKER


@pytest.mark.asyncio
async def test_dispatch_to_async_callback(ws_client):
    """Test dispatch to asynchronous callback."""
    messages = []

    async def callback(msg: Message):
        messages.append(msg)

    ws_client.subscribe(ChannelType.TICKER, "BTC", callback)

    # Add message to queue
    await ws_client._handle_message(
        {
            "channel": "ticker",
            "symbol": "BTC",
            "data": {"price": 50000},
        }
    )

    # Manually trigger one dispatch cycle
    ws_client._running = True
    key = "ticker:BTC"
    if key in ws_client._subscriptions and ws_client._queues[key]:
        _, _, cb = ws_client._subscriptions[key]
        msg = ws_client._queues[key].popleft()
        await cb(msg)

    assert len(messages) == 1
    assert messages[0].symbol == "BTC"


@pytest.mark.asyncio
async def test_invalid_channel(ws_client, caplog):
    """Test handling of invalid channel."""
    await ws_client._handle_message(
        {
            "channel": "invalid_channel",
            "data": {},
        }
    )

    assert "Unknown channel" in caplog.text


@pytest.mark.asyncio
async def test_missing_channel(ws_client, caplog):
    """Test handling of message without channel."""
    await ws_client._handle_message(
        {
            "data": {"price": 50000},
        }
    )

    assert "Message missing channel" in caplog.text


def test_config_redacts_api_key_and_rejects_private_destinations():
    config = WebSocketConfig(api_key="secret-token")
    assert "secret-token" not in repr(config)
    with pytest.raises(ValueError):
        WebSocketConfig(url="wss://127.0.0.1/ws")


@pytest.mark.asyncio
async def test_sequence_gap_invalidates_buffer_and_marks_degraded(ws_client):
    ws_client.subscribe(ChannelType.ORDERBOOK, "BTC", Mock())
    await ws_client._handle_message(
        {"channel": "orderbook", "symbol": "BTC", "seq": 10, "data": {"bids": []}}
    )
    assert len(ws_client._queues["orderbook:BTC"]) == 1

    await ws_client._handle_message(
        {"channel": "orderbook", "symbol": "BTC", "seq": 12, "data": {"bids": []}}
    )

    stats = ws_client.get_stats()
    assert len(ws_client._queues["orderbook:BTC"]) == 0
    assert stats["sequence_gaps"]["orderbook:BTC"] == 1
    assert "orderbook:BTC" in stats["degraded_subscriptions"]


@pytest.mark.asyncio
async def test_callback_execution_is_bounded(ws_client):
    ws_client.config.callback_timeout = 0.05

    async def stalled(_message):
        await __import__("asyncio").sleep(0.2)

    message = Message(ChannelType.TICKER, "BTC", {"price": 1})
    with pytest.raises(TimeoutError):
        await ws_client._invoke_callback(stalled, message)


@pytest.mark.asyncio
async def test_connect_failure_does_not_log_exception_body(ws_client, monkeypatch, caplog):
    async def fail_connect(*_args, **_kwargs):
        raise RuntimeError("authorization=secret-token")

    monkeypatch.setattr("trading_gym.adapters.aban_websocket.websockets.connect", fail_connect)
    with caplog.at_level("ERROR"):
        with pytest.raises(RuntimeError):
            await ws_client.connect()
    assert "secret-token" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_connection_kwargs_disable_ambient_proxy(ws_client):
    kwargs = ws_client._connect_kwargs({"Authorization": "Token secret"})
    assert kwargs.get("proxy") is None
    header_key = "additional_headers" if "additional_headers" in kwargs else "extra_headers"
    assert kwargs[header_key]["Authorization"] == "Token secret"
