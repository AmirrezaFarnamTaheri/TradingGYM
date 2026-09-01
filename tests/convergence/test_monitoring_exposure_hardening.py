from __future__ import annotations

import base64
import http.client
import threading
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect

from apps.monitoring import exporter


class FakeWebSocket:
    def __init__(self, *, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}
        self.closed = []

    async def close(self, **kwargs):
        self.closed.append(kwargs)


@pytest.mark.asyncio
async def test_websocket_query_credentials_are_never_accepted(monkeypatch):
    from apps.api import main as api

    monkeypatch.setattr(api, "API_KEY", "correct-secret")
    monkeypatch.setattr(api, "API_KEY_CONFIGURED", True)
    socket = FakeWebSocket(query={"api_key": "correct-secret"})
    with pytest.raises(WebSocketDisconnect):
        await api.authenticate_websocket(socket)
    assert socket.closed and socket.closed[-1]["code"] == 1008


@pytest.mark.asyncio
async def test_websocket_subprotocol_authentication_succeeds(monkeypatch):
    from apps.api import main as api
    from apps.api.runtime import WS_PROTOCOL

    monkeypatch.setattr(api, "API_KEY", "correct-secret")
    monkeypatch.setattr(api, "API_KEY_CONFIGURED", True)
    encoded = base64.urlsafe_b64encode(b"correct-secret").decode().rstrip("=")
    protocol = f"{WS_PROTOCOL}, auth.{encoded}"
    socket = FakeWebSocket(headers={"sec-websocket-protocol": protocol})
    selected = await api.authenticate_websocket(socket)
    assert selected == WS_PROTOCOL
    assert not socket.closed


def test_metrics_exporter_requires_explicit_public_exposure(monkeypatch):
    monkeypatch.delenv("METRICS_EXPORTER_TOKEN", raising=False)
    with pytest.raises(Exception, match="allow-public-bind"):
        exporter.main(port=19090, host="0.0.0.0", allow_public_bind=False)
    with pytest.raises(Exception, match="METRICS_EXPORTER_TOKEN"):
        exporter.main(port=19090, host="0.0.0.0", allow_public_bind=True)


def test_metrics_endpoint_enforces_bearer_token(monkeypatch):
    token = "x" * 32
    server = exporter.MetricsServer(
        ("127.0.0.1", 0), exporter.MetricsHandler, bearer_token=token
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=3)
        connection.request("GET", "/metrics")
        response = connection.getresponse()
        assert response.status == 401
        response.read()
        connection.close()

        connection = http.client.HTTPConnection(host, port, timeout=3)
        connection.request("GET", "/metrics", headers={"Authorization": f"Bearer {token}"})
        response = connection.getresponse()
        assert response.status == 200
        assert b"# HELP" in response.read()
        connection.close()

        connection = http.client.HTTPConnection(host, port, timeout=3)
        connection.request("GET", "/health")
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == b"OK\n"
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
