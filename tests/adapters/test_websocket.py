"""Tests for WebSocket client."""

from collections import deque
from unittest.mock import Mock

import pytest

from trading_gym.market.adapters.aban_websocket import (
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


class _FakeSocket:
    """Minimal stand-in for a connected websockets protocol object."""

    def __init__(self, peer, *, send_fails: bool = False):
        self.remote_address = peer
        self.send_fails = send_fails
        self.sent: list[str] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        if self.send_fails:
            raise RuntimeError("send failed")
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True


def _client_with_peer_policy(*, allow_private: bool) -> WebSocketClient:
    config = WebSocketConfig(
        url="wss://8.8.8.8/ws",
        api_key="test-key",
        reconnect_delay_base=0.1,
        reconnect_delay_max=1.0,
        heartbeat_interval=0.5,
        heartbeat_timeout=2.0,
        allow_private_url=allow_private,
    )
    return WebSocketClient(config)


@pytest.mark.asyncio
async def test_connect_rejects_a_private_peer_and_closes_the_socket(monkeypatch):
    """The pre-connect URL check cannot close the TOCTOU window on its own: the
    library re-resolves the hostname when it opens the socket, so the peer the
    connection actually lands on must be re-checked, and must not be left
    connected when it fails."""
    socket = _FakeSocket(("127.0.0.1", 443))

    async def fake_connect(*_args, **_kwargs):
        return socket

    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket._open_no_redirect", fake_connect
    )
    client = _client_with_peer_policy(allow_private=False)

    with pytest.raises(ValueError, match="disallowed"):
        await client.connect()

    assert client._ws is None
    assert client._connected is False
    assert socket.closed, "a rejected peer must not be left connected"


@pytest.mark.asyncio
async def test_connect_rejects_an_unverifiable_peer(monkeypatch):
    """A peer the library will not expose an address for fails closed."""
    socket = _FakeSocket(None)

    async def fake_connect(*_args, **_kwargs):
        return socket

    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket._open_no_redirect", fake_connect
    )
    client = _client_with_peer_policy(allow_private=True)

    with pytest.raises(ValueError, match="verifiable peer address"):
        await client.connect()
    assert socket.closed
    assert client._ws is None


@pytest.mark.asyncio
async def test_connect_allows_a_public_peer_and_subscribes(monkeypatch):
    """A peer in an allowed range connects normally and sends subscriptions."""
    socket = _FakeSocket(("8.8.8.8", 443))  # a public address

    async def fake_connect(*_args, **_kwargs):
        return socket

    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket._open_no_redirect", fake_connect
    )
    client = _client_with_peer_policy(allow_private=False)
    client.subscribe(ChannelType.TICKER, "BTC", lambda _msg: None)

    await client.connect()

    assert client._connected is True
    assert client._ws is socket
    assert len(socket.sent) == 1
    assert "subscribe" in socket.sent[0]
    await client.disconnect()


@pytest.mark.asyncio
async def test_connect_closes_the_socket_when_subscription_send_fails(monkeypatch):
    """A socket that fails mid-handshake is closed, not orphaned."""
    socket = _FakeSocket(("8.8.8.8", 443), send_fails=True)

    async def fake_connect(*_args, **_kwargs):
        return socket

    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket._open_no_redirect", fake_connect
    )
    client = _client_with_peer_policy(allow_private=False)
    client.subscribe(ChannelType.TICKER, "BTC", lambda _msg: None)

    with pytest.raises(RuntimeError, match="send failed"):
        await client.connect()

    assert client._ws is None
    assert socket.closed


@pytest.mark.asyncio
async def test_connect_failure_does_not_log_exception_body(
    ws_client, monkeypatch, caplog
):
    async def fail_connect(*_args, **_kwargs):
        raise RuntimeError("authorization=secret-token")

    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket._open_no_redirect", fail_connect
    )
    with caplog.at_level("ERROR"):
        with pytest.raises(RuntimeError):
            await ws_client.connect()
    assert "secret-token" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_connection_kwargs_disable_ambient_proxy(ws_client):
    kwargs = ws_client._connect_kwargs({"Authorization": "Token secret"})
    assert kwargs.get("proxy") is None
    header_key = (
        "additional_headers" if "additional_headers" in kwargs else "extra_headers"
    )
    assert kwargs[header_key]["Authorization"] == "Token secret"


@pytest.mark.asyncio
async def test_callback_failure_accounts_the_dropped_message(ws_client):
    """A message whose callback raises is already popped and permanently lost;
    drop telemetry must count it so message loss is not underreported."""

    def callback(_message: Message) -> None:
        ws_client._running = False  # let the dispatch loop exit after this cycle
        raise RuntimeError("boom")

    ws_client.subscribe(ChannelType.TICKER, "BTC", callback)
    await ws_client._handle_message(
        {"channel": "ticker", "symbol": "BTC", "data": {"price": 1}}
    )
    key = "ticker:BTC"
    assert len(ws_client._queues[key]) == 1

    ws_client._running = True
    ws_client._connected = True
    await ws_client._dispatch_loop()

    assert ws_client._queues[key] == deque()
    stats = ws_client.get_stats()
    assert stats["callback_failures"][key] == 1
    assert stats["dropped_messages"][key] == 1
    assert key in stats["degraded_subscriptions"]


@pytest.mark.asyncio
async def test_public_ws_credentials_require_tls_before_connect(monkeypatch):
    config = WebSocketConfig(url="ws://8.8.8.8/ws", api_key="private-token")
    client = WebSocketClient(config)
    calls = []

    async def fake_connect(*_args, **_kwargs):
        calls.append("handshake")
        return _FakeSocket(("8.8.8.8", 80))

    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket._open_no_redirect", fake_connect
    )
    with pytest.raises(ValueError, match="requires TLS"):
        await client.connect()
    assert calls == []


@pytest.mark.asyncio
async def test_mixed_private_dns_answer_cannot_receive_auth_header(monkeypatch):
    import socket

    config = WebSocketConfig(url="wss://venue.example/ws", api_key="sensitive-token")
    client = WebSocketClient(config)
    calls = []

    def mixed_dns(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    async def fake_connect(*_args, **_kwargs):
        calls.append("handshake")
        return _FakeSocket(("127.0.0.1", 443))

    monkeypatch.setattr(socket, "getaddrinfo", mixed_dns)
    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket._open_no_redirect", fake_connect
    )
    with pytest.raises(ValueError, match="disallowed"):
        await client.connect()
    assert calls == []


@pytest.mark.asyncio
async def test_websocket_auth_handshake_uses_validated_pinned_ip(monkeypatch):
    import socket

    config = WebSocketConfig(url="wss://venue.example/ws", api_key="sensitive-token")
    client = WebSocketClient(config)
    captures = []

    def approved_dns(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

    async def fake_connect(url, **kwargs):
        captures.append((url, kwargs))
        return _FakeSocket(("8.8.8.8", 443))

    monkeypatch.setattr(socket, "getaddrinfo", approved_dns)
    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket._open_no_redirect", fake_connect
    )
    await client.connect()
    assert len(captures) == 1
    uri, params = captures[0]
    assert uri == "wss://venue.example/ws"
    assert (params["host"], params["port"]) == ("8.8.8.8", 443)
    header_key = (
        "additional_headers" if "additional_headers" in params else "extra_headers"
    )
    assert params[header_key]["Authorization"] == "Token sensitive-token"
    await client.disconnect()


@pytest.mark.asyncio
async def test_private_opt_in_does_not_allow_public_plaintext_credentials(monkeypatch):
    config = WebSocketConfig(
        url="ws://8.8.8.8/ws",
        api_key="private-token",
        allow_private_url=True,
    )
    client = WebSocketClient(config)
    sent = []

    async def fake_connect(*_args, **_kwargs):
        sent.append("credential_handshake")
        return _FakeSocket(("8.8.8.8", 80))

    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket._open_no_redirect", fake_connect
    )
    with pytest.raises(ValueError, match="requires TLS"):
        await client.connect()
    assert not sent


@pytest.mark.asyncio
async def test_legacy_redirect_is_rejected_before_second_handshake(monkeypatch):
    from trading_gym.market.adapters.aban_websocket import _open_no_redirect

    class LegacyConnector:
        def handle_redirect(self, uri):
            raise AssertionError("original redirect handler was not replaced")

        def __await__(self):
            async def second_connection():
                self.handle_redirect("wss://127.0.0.1/private")

            return second_connection().__await__()

    legacy = LegacyConnector()
    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket.websockets.connect",
        lambda *_args, **_kwargs: legacy,
    )
    with pytest.raises(ValueError, match="websocket_redirect_forbidden"):
        await _open_no_redirect(
            "wss://venue.example/ws",
            host="8.8.8.8",
            port=443,
            extra_headers={"Authorization": "Token confidential"},
        )


@pytest.mark.asyncio
async def test_modern_redirect_is_rejected_before_second_handshake(monkeypatch):
    from trading_gym.market.adapters.aban_websocket import _open_no_redirect

    class ModernConnector:
        def process_redirect(self, exc):
            return "wss://127.0.0.1/private"

        def __await__(self):
            async def second_connection():
                action = self.process_redirect(RuntimeError("302"))
                if isinstance(action, Exception):
                    raise action
                raise AssertionError("unauthorized redirected handshake")

            return second_connection().__await__()

    modern = ModernConnector()
    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket.websockets.connect",
        lambda *_args, **_kwargs: modern,
    )
    with pytest.raises(ValueError, match="websocket_redirect_forbidden"):
        await _open_no_redirect(
            "wss://venue.example/ws",
            host="8.8.8.8",
            port=443,
            additional_headers={"Authorization": "Token confidential"},
        )


@pytest.mark.asyncio
async def test_unknown_connector_cannot_bypass_redirect_policy(monkeypatch):
    from trading_gym.market.adapters.aban_websocket import _open_no_redirect

    class UnknownConnector:
        async def __call__(self):
            raise AssertionError("connection should never be opened")

    monkeypatch.setattr(
        "trading_gym.market.adapters.aban_websocket.websockets.connect",
        lambda *_args, **_kwargs: UnknownConnector(),
    )
    with pytest.raises(RuntimeError, match="unsupported_websocket_redirect_policy"):
        await _open_no_redirect("wss://venue.example/ws", host="8.8.8.8", port=443)


def test_protocol_debug_cannot_log_credential_headers_under_global_debug(monkeypatch):
    import logging

    ws_client = WebSocketClient(
        WebSocketConfig(url="wss://8.8.8.8/ws", api_key="sensitive-token")
    )
    root_logger = logging.getLogger()
    monkeypatch.setattr(root_logger, "level", logging.DEBUG)
    kwargs = ws_client._connect_kwargs({"Authorization": "Token sensitive-token"})
    assert not kwargs["logger"].isEnabledFor(logging.DEBUG)
    assert kwargs["logger"].getEffectiveLevel() >= logging.WARNING
    assert kwargs["logger"] is not root_logger
