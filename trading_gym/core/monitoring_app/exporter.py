#!/usr/bin/env python3
"""Bounded Prometheus metrics exporter.

The exporter binds to loopback by default.  Public exposure requires both an
explicit flag and a bearer token supplied through ``METRICS_EXPORTER_TOKEN``.
"""

from __future__ import annotations

import logging
import os
import secrets
import signal
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlsplit

import typer
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from trading_gym.core.monitoring.metrics import TradingMetrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
app = typer.Typer()
_metrics_instance: Optional[TradingMetrics] = None
_MAX_METRICS_BYTES = 16 * 1024 * 1024


def get_metrics_instance() -> TradingMetrics:
    global _metrics_instance
    if _metrics_instance is None:
        _metrics_instance = TradingMetrics()
    return _metrics_instance


class MetricsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, bearer_token: str | None):
        super().__init__(address, handler)
        self.bearer_token = bearer_token


class MetricsHandler(BaseHTTPRequestHandler):
    """Serve fixed, non-reflective monitoring endpoints."""

    protocol_version = "HTTP/1.1"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("Monitoring client disconnected during response")

    def _authorised(self) -> bool:
        expected = getattr(self.server, "bearer_token", None)
        if not expected:
            return True
        supplied = self.headers.get("Authorization", "")
        prefix = "Bearer "
        return supplied.startswith(prefix) and secrets.compare_digest(
            supplied[len(prefix) :], expected
        )

    def do_GET(self) -> None:
        try:
            path = urlsplit(self.path).path
            if path == "/metrics":
                if not self._authorised():
                    self._send(401, b"Unauthorized\n", "text/plain; charset=utf-8")
                    return
                output = generate_latest(get_metrics_instance().registry)
                if len(output) > _MAX_METRICS_BYTES:
                    logger.error("Metrics payload exceeds configured byte limit")
                    self._send(
                        503, b"Metrics unavailable\n", "text/plain; charset=utf-8"
                    )
                    return
                self._send(200, output, CONTENT_TYPE_LATEST)
                return
            if path in {"/health", "/healthz"}:
                self._send(200, b"OK\n", "text/plain; charset=utf-8")
                return
            self._send(404, b"Not Found\n", "text/plain; charset=utf-8")
        except Exception as exc:
            logger.error("Monitoring request failed error_type=%s", type(exc).__name__)
            try:
                self._send(500, b"Internal Server Error\n", "text/plain; charset=utf-8")
            except Exception as send_exc:
                logger.error(
                    "Monitoring error response failed error_type=%s",
                    type(send_exc).__name__,
                )

    def log_message(self, format: str, *args) -> None:
        # Avoid reverse-DNS lookups and bound peer-controlled request text.
        peer = str(self.client_address[0])[:64]
        rendered = (format % args).replace("\r", " ").replace("\n", " ")[:512]
        logger.info("monitoring_client=%s request=%s", peer, rendered)


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False
    try:
        import ipaddress

        return bool(addresses) and all(
            ipaddress.ip_address(address).is_loopback for address in addresses
        )
    except ValueError:
        return False


@app.command()
def main(
    port: int = typer.Option(9090, "--port", "-p", min=1, max=65535),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
    allow_public_bind: bool = typer.Option(
        False,
        "--allow-public-bind",
        help="Permit a non-loopback bind; requires METRICS_EXPORTER_TOKEN",
    ),
) -> None:
    """Run the Prometheus exporter with fail-safe exposure defaults."""
    host = host.strip()
    if not host or any(ord(char) < 32 for char in host):
        raise typer.BadParameter("host is invalid")
    loopback = _is_loopback_host(host)
    bearer_token = os.getenv("METRICS_EXPORTER_TOKEN")
    if not loopback:
        if not allow_public_bind:
            raise typer.BadParameter("non-loopback bind requires --allow-public-bind")
        if not bearer_token or len(bearer_token) < 24:
            raise typer.BadParameter(
                "public metrics exposure requires METRICS_EXPORTER_TOKEN of at least 24 characters"
            )
        logger.warning("Prometheus exporter is explicitly exposed beyond loopback")

    _ = get_metrics_instance()
    server = MetricsServer((host, port), MetricsHandler, bearer_token=bearer_token)

    def signal_handler(_sig, _frame):
        logger.info("Shutting down metrics exporter")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    logger.info("Prometheus exporter listening on %s:%d", host, port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logger.info("Metrics exporter stopped")


if __name__ == "__main__":
    app()
