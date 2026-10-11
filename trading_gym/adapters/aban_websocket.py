"""Bounded, integrity-aware Abantether WebSocket client.

The client treats sequence gaps, queue loss, heartbeat expiry, and callback
failure as observable degradation rather than harmless log noise.  It never
logs credential-bearing exception bodies and validates the destination both at
configuration time and immediately before connecting.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import re
import socket
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Deque, Dict, Optional
from urllib.parse import urlparse

import websockets

from trading_gym.common.security import (
    bounded_json_dumps,
    validate_base_url,
    validate_peer_address,
)

try:  # websockets >=12 exposes legacy API under websockets.legacy
    from websockets.legacy.client import WebSocketClientProtocol
except ImportError:  # pragma: no cover - fallback for older versions
    from websockets.client import WebSocketClientProtocol  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class ChannelType(str, Enum):
    """Available WebSocket channels."""

    TICKER = "ticker"
    ORDERBOOK = "orderbook"
    TRADES = "trades"
    USER_DATA = "user"


Callback = Callable[["Message"], None | Awaitable[None]]


def _validate_websocket_url(
    url: str,
    *,
    allow_private: bool,
    resolve_dns: bool,
) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"ws", "wss"}:
        raise ValueError("WebSocket URL must use ws:// or wss://")
    translated = parsed._replace(
        scheme="https" if parsed.scheme == "wss" else "http"
    ).geturl()
    validated = validate_base_url(
        translated,
        allow_private=allow_private,
        allow_loopback=allow_private,
        resolve_dns=resolve_dns,
    )
    translated_back = urlparse(validated)._replace(scheme=parsed.scheme).geturl()
    return translated_back


async def _validated_tcp_target(url: str, *, allow_private: bool) -> tuple[str, int]:
    """Resolve once and pin a vetted IP for the credential-bearing handshake.

    The websocket library otherwise re-resolves the hostname after the policy
    check. All DNS answers are checked; the selected IP becomes the actual
    TCP target, while the URI retains its original Host and TLS server name.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError("WebSocket URL is missing a hostname")
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    try:
        resolved = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise ValueError("WebSocket hostname could not be resolved") from exc
    if not resolved:
        raise ValueError("WebSocket hostname returned no addresses")
    addresses = sorted({record[4][0] for record in resolved})
    for address in addresses:
        validate_peer_address(
            address, allow_private=allow_private, allow_loopback=allow_private
        )
    return addresses[0], port


@dataclass
class WebSocketConfig:
    """Validated configuration for :class:`WebSocketClient`."""

    url: str = "wss://api.abantether.com/ws"
    api_key: Optional[str] = field(default=None, repr=False)
    reconnect_delay_base: float = 2.0
    reconnect_delay_max: float = 60.0
    reconnect_jitter: float = 0.5
    heartbeat_interval: float = 30.0
    heartbeat_timeout: float = 60.0
    message_queue_maxlen: int = 10_000
    max_message_bytes: int = 1_048_576
    callback_timeout: float = 5.0
    max_subscriptions: int = 256
    orderbook_aggregate: bool = True
    max_reconnect_attempts: int = 0
    allow_private_url: bool = False

    def __post_init__(self) -> None:
        self.url = _validate_websocket_url(
            self.url,
            allow_private=self.allow_private_url,
            resolve_dns=False,
        )
        if self.api_key is not None:
            self.api_key = self.api_key.strip() or None
        if not 0.05 <= float(self.reconnect_delay_base) <= 300.0:
            raise ValueError(
                "reconnect_delay_base must be between 0.05 and 300 seconds"
            )
        if not self.reconnect_delay_base <= float(self.reconnect_delay_max) <= 600.0:
            raise ValueError("reconnect_delay_max must be >= base and <= 600 seconds")
        if not 0.0 <= float(self.reconnect_jitter) <= 1.0:
            raise ValueError("reconnect_jitter must be between 0 and 1")
        if not 0.05 <= float(self.heartbeat_interval) <= 300.0:
            raise ValueError("heartbeat_interval must be between 0.05 and 300 seconds")
        if not self.heartbeat_interval < float(self.heartbeat_timeout) <= 900.0:
            raise ValueError(
                "heartbeat_timeout must exceed heartbeat_interval and be <= 900"
            )
        if (
            isinstance(self.message_queue_maxlen, bool)
            or not 1 <= self.message_queue_maxlen <= 100_000
        ):
            raise ValueError("message_queue_maxlen must be between 1 and 100000")
        if (
            isinstance(self.max_message_bytes, bool)
            or not 1_024 <= self.max_message_bytes <= 16 * 1024 * 1024
        ):
            raise ValueError("max_message_bytes must be between 1024 and 16777216")
        if not 0.05 <= float(self.callback_timeout) <= 60.0:
            raise ValueError("callback_timeout must be between 0.05 and 60 seconds")
        if (
            isinstance(self.max_subscriptions, bool)
            or not 1 <= self.max_subscriptions <= 1_000
        ):
            raise ValueError("max_subscriptions must be between 1 and 1000")
        if (
            isinstance(self.max_reconnect_attempts, bool)
            or not 0 <= self.max_reconnect_attempts <= 100_000
        ):
            raise ValueError("max_reconnect_attempts must be between 0 and 100000")


@dataclass(frozen=True)
class Message:
    """Validated message delivered to a subscriber."""

    channel: ChannelType
    symbol: Optional[str]
    data: Dict[str, Any]
    sequence: Optional[int] = None
    timestamp: float = field(default_factory=time.time)


class WebSocketClient:
    """Asynchronous client with bounded queues and integrity telemetry."""

    def __init__(self, config: WebSocketConfig):
        self.config = config
        self._ws: Optional[WebSocketClientProtocol] = None
        self._running = False
        self._connected = False
        self._reconnect_count = 0
        self._last_heartbeat = 0.0
        self._last_heartbeat_response = 0.0

        self._queues: Dict[str, Deque[Message]] = {}
        self._subscriptions: Dict[str, tuple[ChannelType, Optional[str], Callback]] = {}
        self._sequences: Dict[str, int] = {}
        self._sequence_gaps: Dict[str, int] = {}
        self._dropped_messages: Dict[str, int] = {}
        self._callback_failures: Dict[str, int] = {}
        self._degraded_keys: set[str] = set()

        self._receive_task: Optional[asyncio.Task[Any]] = None
        self._heartbeat_task: Optional[asyncio.Task[Any]] = None
        self._dispatch_task: Optional[asyncio.Task[Any]] = None

        logger.info("Initialized WebSocketClient with url=%s", config.url)

    @staticmethod
    def _validate_symbol(symbol: Optional[str]) -> Optional[str]:
        if symbol is None:
            return None
        normalized = str(symbol).strip()
        if not _SYMBOL_RE.fullmatch(normalized):
            raise ValueError("symbol must contain 1-32 safe alphanumeric characters")
        return normalized

    def subscribe(
        self,
        channel: ChannelType,
        symbol: Optional[str],
        callback: Callback,
    ) -> None:
        """Register a bounded local subscription.

        Call this before :meth:`run`.  Re-registering an existing key updates
        the callback without discarding already queued messages.
        """
        if not isinstance(channel, ChannelType):
            channel = ChannelType(channel)
        symbol = self._validate_symbol(symbol)
        if not callable(callback):
            raise TypeError("callback must be callable")
        if channel is ChannelType.USER_DATA and not self.config.api_key:
            raise ValueError("USER_DATA subscriptions require an API key")
        key = self._make_key(channel, symbol)
        if (
            key not in self._subscriptions
            and len(self._subscriptions) >= self.config.max_subscriptions
        ):
            raise ValueError("subscription limit exceeded")
        self._subscriptions[key] = (channel, symbol, callback)
        self._queues.setdefault(key, deque(maxlen=self.config.message_queue_maxlen))
        logger.info("Subscribed to %s:%s", channel.value, symbol or "all")

    def unsubscribe(self, channel: ChannelType, symbol: Optional[str]) -> None:
        """Remove a local subscription and its buffered data."""
        symbol = self._validate_symbol(symbol)
        key = self._make_key(channel, symbol)
        self._subscriptions.pop(key, None)
        self._queues.pop(key, None)
        self._sequences.pop(key, None)
        self._degraded_keys.discard(key)
        logger.info("Unsubscribed from %s:%s", channel.value, symbol or "all")

    def _connect_kwargs(self, headers: Dict[str, str]) -> Dict[str, Any]:
        parameters = inspect.signature(websockets.connect).parameters
        kwargs: Dict[str, Any] = {
            "ping_interval": None,
            "open_timeout": min(30.0, self.config.heartbeat_timeout),
            "close_timeout": 10.0,
            "max_size": self.config.max_message_bytes,
            "max_queue": 32,
        }
        if "additional_headers" in parameters:
            kwargs["additional_headers"] = headers
        else:  # websockets < 14
            kwargs["extra_headers"] = headers
        # Avoid inheriting a process-wide proxy that could receive credentials.
        if "proxy" in parameters:
            kwargs["proxy"] = None
        return kwargs

    async def connect(self) -> None:
        """Establish a connection after immediate destination revalidation."""
        if self._connected:
            return
        _validate_websocket_url(
            self.config.url,
            allow_private=self.config.allow_private_url,
            resolve_dns=False,
        )
        if (
            self.config.api_key
            and urlparse(self.config.url).scheme != "wss"
            and not self.config.allow_private_url
        ):
            raise ValueError("authenticated public WebSocket requires TLS")
        tcp_host, tcp_port = await _validated_tcp_target(
            self.config.url, allow_private=self.config.allow_private_url
        )
        headers: Dict[str, str] = {}
        if self.config.api_key:
            headers["Authorization"] = f"Token {self.config.api_key}"
        try:
            self._ws = await websockets.connect(
                self.config.url,
                host=tcp_host,
                port=tcp_port,
                **self._connect_kwargs(headers),
            )
            # A vetted IP is used for TCP; URI hostname still controls Host
            # and TLS certificate verification. Verify the observed peer as
            # an additional check before sending subscription messages.
            self._verify_peer(self._ws)
            self._connected = True
            self._reconnect_count = 0
            self._last_heartbeat_response = time.monotonic()
            await self._send_subscriptions()
            logger.info("WebSocket connected")
        except Exception as exc:
            ws, self._ws = self._ws, None
            self._connected = False
            if ws is not None:
                # A rejected peer must not be left connected, and neither must
                # a socket that failed mid-handshake.
                with suppress(Exception):
                    await ws.close()
            logger.error(
                "WebSocket connection failed error_type=%s", type(exc).__name__
            )
            raise

    async def disconnect(self) -> None:
        """Close the connection and cancel all owned tasks."""
        self._running = False
        self._connected = False
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception as exc:
                logger.warning(
                    "WebSocket close failed error_type=%s", type(exc).__name__
                )

        current = asyncio.current_task()
        for task in (self._receive_task, self._heartbeat_task, self._dispatch_task):
            if task and task is not current and not task.done():
                task.cancel()
        for task in (self._receive_task, self._heartbeat_task, self._dispatch_task):
            if task and task is not current:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        logger.info("WebSocket disconnected")

    async def run(self) -> None:
        """Run until cancelled, disconnected, or reconnect budget is exhausted."""
        if self._running:
            raise RuntimeError("WebSocket client is already running")
        self._running = True
        try:
            while self._running:
                if not self._connected and not await self._reconnect():
                    continue
                if not self._running or not self._connected:
                    continue

                tasks = [
                    asyncio.create_task(self._receive_loop(), name="aban-ws-receive"),
                    asyncio.create_task(
                        self._heartbeat_loop(), name="aban-ws-heartbeat"
                    ),
                    asyncio.create_task(self._dispatch_loop(), name="aban-ws-dispatch"),
                ]
                self._receive_task, self._heartbeat_task, self._dispatch_task = tasks
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc is not None:
                        logger.error(
                            "WebSocket task failed error_type=%s", type(exc).__name__
                        )
                if self._running:
                    self._connected = False
                    ws, self._ws = self._ws, None
                    if ws is not None:
                        try:
                            await ws.close()
                        except Exception:
                            pass
                    logger.warning("WebSocket connection lost; reconnecting")
        except asyncio.CancelledError:
            logger.info("WebSocket client cancelled")
            raise
        finally:
            await self.disconnect()

    def _verify_peer(self, ws: Any) -> None:
        """Reject a connection whose *actual* peer address is not allowed."""

        peer = getattr(ws, "remote_address", None)
        host: str | None = None
        if isinstance(peer, tuple) and peer:
            host = peer[0] if isinstance(peer[0], str) else None
        if not host:
            # No address to verify (an unusual transport, or a library that
            # does not expose it). Fail closed rather than trust what cannot
            # be checked, the same way a missing URL is treated.
            raise ValueError("WebSocket connection has no verifiable peer address")
        validate_peer_address(
            host,
            allow_private=self.config.allow_private_url,
            allow_loopback=self.config.allow_private_url,
        )

    async def _send_subscriptions(self) -> None:
        if not self._ws:
            raise RuntimeError("cannot subscribe without an active connection")
        for channel, symbol, _ in list(self._subscriptions.values()):
            payload: Dict[str, Any] = {"action": "subscribe", "channel": channel.value}
            if symbol:
                payload["symbol"] = symbol
            await self._ws.send(bounded_json_dumps(payload, max_bytes=4_096))

    async def _reconnect(self) -> bool:
        if (
            self.config.max_reconnect_attempts
            and self._reconnect_count >= self.config.max_reconnect_attempts
        ):
            logger.error("WebSocket reconnect budget exhausted")
            self._running = False
            return False

        exponent = min(self._reconnect_count, 20)
        delay = min(
            self.config.reconnect_delay_base * (2**exponent),
            self.config.reconnect_delay_max,
        )
        jitter_span = delay * self.config.reconnect_jitter
        delay = max(0.0, delay + random.uniform(-jitter_span, jitter_span))
        logger.info(
            "Reconnecting after %.2fs attempt=%d", delay, self._reconnect_count + 1
        )
        await asyncio.sleep(delay)
        if not self._running:
            return False
        try:
            await self.connect()
            return True
        except Exception as exc:
            self._reconnect_count += 1
            logger.warning("Reconnect failed error_type=%s", type(exc).__name__)
            return False

    async def _receive_loop(self) -> None:
        if not self._ws:
            raise RuntimeError("receive loop started without a connection")
        try:
            async for raw_message in self._ws:
                try:
                    if isinstance(raw_message, bytes):
                        if len(raw_message) > self.config.max_message_bytes:
                            raise ValueError("message exceeds configured limit")
                        raw_message = raw_message.decode("utf-8")
                    if not isinstance(raw_message, str):
                        raise TypeError("unsupported WebSocket message type")
                    if len(raw_message.encode("utf-8")) > self.config.max_message_bytes:
                        raise ValueError("message exceeds configured limit")
                    decoded = json.loads(raw_message)
                    if not isinstance(decoded, dict):
                        raise ValueError("message root must be an object")
                    await self._handle_message(decoded)
                except json.JSONDecodeError:
                    logger.warning("Discarded invalid WebSocket JSON")
                except (TypeError, ValueError) as exc:
                    logger.warning(
                        "Discarded invalid WebSocket message error_type=%s",
                        type(exc).__name__,
                    )
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Receive loop failed error_type=%s", type(exc).__name__)
            raise

    async def _handle_message(self, data: Dict[str, Any]) -> None:
        if data.get("type") == "pong":
            self._last_heartbeat_response = time.monotonic()
            return

        channel_value = data.get("channel")
        if not isinstance(channel_value, str):
            logger.warning("Message missing channel; discarded")
            return
        try:
            channel = ChannelType(channel_value)
        except ValueError:
            logger.warning("Unknown channel; message discarded")
            return

        try:
            symbol = self._validate_symbol(data.get("symbol"))
        except ValueError:
            logger.warning("Discarded WebSocket message with invalid symbol")
            return
        payload = data.get("data", {})
        if not isinstance(payload, dict):
            logger.warning("Discarded WebSocket message with non-object data")
            return
        bounded_json_dumps(payload, max_bytes=self.config.max_message_bytes)

        key = self._make_key(channel, symbol)
        if key not in self._queues:
            return

        sequence_raw = data.get("seq")
        sequence: Optional[int]
        if sequence_raw is None:
            sequence = None
        elif (
            isinstance(sequence_raw, bool)
            or not isinstance(sequence_raw, int)
            or sequence_raw < 0
        ):
            logger.warning(
                "Discarded WebSocket message with invalid sequence key=%s", key
            )
            return
        else:
            sequence = sequence_raw
            expected = self._sequences.get(key)
            if expected is not None and sequence != expected:
                self._sequence_gaps[key] = self._sequence_gaps.get(key, 0) + 1
                self._degraded_keys.add(key)
                self._queues[key].clear()
                self._sequences[key] = sequence + 1
                logger.warning(
                    "Sequence gap detected key=%s expected=%d got=%d; buffer invalidated",
                    key,
                    expected,
                    sequence,
                )
                return
            self._sequences[key] = sequence + 1

        queue = self._queues[key]
        if len(queue) >= self.config.message_queue_maxlen:
            self._dropped_messages[key] = self._dropped_messages.get(key, 0) + 1
            self._degraded_keys.add(key)
            logger.warning(
                "WebSocket queue overflow key=%s; oldest message dropped", key
            )
        queue.append(
            Message(
                channel=channel,
                symbol=symbol,
                data=payload,
                sequence=sequence,
            )
        )

    async def _invoke_callback(self, callback: Callback, message: Message) -> None:
        if inspect.iscoroutinefunction(callback):
            await asyncio.wait_for(callback(message), timeout=self.config.callback_timeout)  # type: ignore[arg-type]
        else:
            await asyncio.wait_for(
                asyncio.to_thread(callback, message),
                timeout=self.config.callback_timeout,
            )

    async def _dispatch_loop(self) -> None:
        while self._running and self._connected:
            delivered = False
            for key, queue in list(self._queues.items()):
                subscription = self._subscriptions.get(key)
                if not queue or subscription is None:
                    continue
                callback = subscription[2]
                while queue and self._running and self._connected:
                    message = queue.popleft()
                    try:
                        await self._invoke_callback(callback, message)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._callback_failures[key] = (
                            self._callback_failures.get(key, 0) + 1
                        )
                        # The message was already popped, so a callback that
                        # raises permanently loses it.  Account it as a drop so
                        # message-loss telemetry is not underreported.
                        self._dropped_messages[key] = (
                            self._dropped_messages.get(key, 0) + 1
                        )
                        self._degraded_keys.add(key)
                        logger.error(
                            "WebSocket callback failed key=%s error_type=%s; message dropped",
                            key,
                            type(exc).__name__,
                        )
                    delivered = True
            await asyncio.sleep(0 if delivered else 0.01)

    async def _heartbeat_loop(self) -> None:
        while self._running and self._connected:
            await asyncio.sleep(self.config.heartbeat_interval)
            now = time.monotonic()
            if now - self._last_heartbeat_response > self.config.heartbeat_timeout:
                self._connected = False
                logger.error("WebSocket heartbeat timeout")
                return
            if self._ws is None:
                self._connected = False
                return
            try:
                await self._ws.send('{"type":"ping"}')
                self._last_heartbeat = now
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                logger.error("Heartbeat send failed error_type=%s", type(exc).__name__)
                return

    @staticmethod
    def _make_key(channel: ChannelType, symbol: Optional[str]) -> str:
        return f"{channel.value}:{symbol or '*'}"

    def get_stats(self) -> Dict[str, Any]:
        """Return non-secret operational and integrity telemetry."""
        return {
            "connected": self._connected,
            "running": self._running,
            "reconnect_count": self._reconnect_count,
            "subscriptions": len(self._subscriptions),
            "queued_messages": {key: len(queue) for key, queue in self._queues.items()},
            "sequence_gaps": dict(self._sequence_gaps),
            "dropped_messages": dict(self._dropped_messages),
            "callback_failures": dict(self._callback_failures),
            "degraded_subscriptions": sorted(self._degraded_keys),
            "last_heartbeat": self._last_heartbeat,
            "last_heartbeat_response": self._last_heartbeat_response,
        }

    def get_latency_ms(self) -> float:
        if (
            self._last_heartbeat <= 0
            or self._last_heartbeat_response < self._last_heartbeat
        ):
            return -1.0
        return (self._last_heartbeat_response - self._last_heartbeat) * 1000.0
