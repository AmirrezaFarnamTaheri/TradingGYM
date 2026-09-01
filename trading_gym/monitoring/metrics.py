"""
Prometheus Metrics
==================

Comprehensive metrics for monitoring trading system health and performance.
"""

import threading
from typing import Optional

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Summary,
    CollectorRegistry,
    generate_latest,
    CONTENT_TYPE_LATEST,
)


class TradingMetrics:
    """
    Centralized metrics collection for trading system.

    Metrics categories:
    - Trading: orders, fills, positions, P&L
    - API: requests, errors, latency
    - System: CPU, memory, latency
    - Risk: margin, leverage, circuit breakers
    - Model: inference time, predictions
    """

    def __init__(self, registry: Optional[CollectorRegistry] = None):
        self.registry = registry or CollectorRegistry()

        # ------------------------------------------------------------------ #
        # Trading Metrics
        # ------------------------------------------------------------------ #

        self.orders_total = Counter(
            "trading_orders_total",
            "Total number of orders placed",
            ["symbol", "side", "order_type"],
            registry=self.registry,
        )

        self.orders_rejected_total = Counter(
            "trading_orders_rejected_total",
            "Total number of orders rejected",
            ["symbol", "side", "rejection_reason"],
            registry=self.registry,
        )

        self.fills_total = Counter(
            "trading_fills_total",
            "Total number of fills",
            ["symbol", "side"],
            registry=self.registry,
        )

        self.fill_latency_seconds = Histogram(
            "trading_fill_latency_seconds",
            "Time from order to fill",
            ["symbol"],
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
            registry=self.registry,
        )

        self.order_latency_seconds = Histogram(
            "trading_order_latency_seconds",
            "Time to place order",
            ["symbol"],
            buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
            registry=self.registry,
        )

        self.position_size = Gauge(
            "trading_position_size",
            "Current position size",
            ["symbol"],
            registry=self.registry,
        )

        self.position_value_usd = Gauge(
            "trading_position_value_usd",
            "Current position value in USD",
            ["symbol"],
            registry=self.registry,
        )

        self.portfolio_value_usd = Gauge(
            "trading_portfolio_value_usd",
            "Total portfolio value in USD",
            registry=self.registry,
        )

        self.pnl_realized_usd = Gauge(
            "trading_pnl_realized_usd",
            "Realized P&L in USD",
            registry=self.registry,
        )

        self.pnl_unrealized_usd = Gauge(
            "trading_pnl_unrealized_usd",
            "Unrealized P&L in USD",
            registry=self.registry,
        )

        self.trading_costs_usd = Counter(
            "trading_costs_usd_total",
            "Trading costs (fees + slippage)",
            ["cost_type"],  # fee, slippage, crossing
            registry=self.registry,
        )

        # ------------------------------------------------------------------ #
        # API Metrics
        # ------------------------------------------------------------------ #

        self.api_requests_total = Counter(
            "api_requests_total",
            "Total API requests",
            ["endpoint", "method", "status"],
            registry=self.registry,
        )

        self.api_errors_total = Counter(
            "api_errors_total",
            "Total API errors",
            ["endpoint", "error_type"],
            registry=self.registry,
        )

        self.api_latency_seconds = Histogram(
            "api_latency_seconds",
            "API request latency",
            ["endpoint"],
            buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
            registry=self.registry,
        )

        self.api_retry_total = Counter(
            "api_retry_total",
            "Total API retries",
            ["endpoint"],
            registry=self.registry,
        )

        self.api_rate_limit_sleep_seconds = Summary(
            "api_rate_limit_sleep_seconds",
            "Time spent sleeping due to rate limits",
            registry=self.registry,
        )

        # ------------------------------------------------------------------ #
        # System Metrics
        # ------------------------------------------------------------------ #

        self.step_duration_seconds = Histogram(
            "system_step_duration_seconds",
            "Environment step duration",
            buckets=[0.0001, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.05],
            registry=self.registry,
        )

        self.process_memory_bytes = Gauge(
            "system_process_memory_bytes",
            "Process memory usage",
            registry=self.registry,
        )

        self.cpu_usage_percent = Gauge(
            "system_cpu_usage_percent",
            "CPU usage percentage",
            registry=self.registry,
        )

        # ------------------------------------------------------------------ #
        # Risk Metrics
        # ------------------------------------------------------------------ #

        self.margin_ratio = Gauge(
            "risk_margin_ratio",
            "Current margin ratio",
            registry=self.registry,
        )

        self.leverage = Gauge(
            "risk_leverage",
            "Current leverage",
            registry=self.registry,
        )

        self.circuit_breaker_trips_total = Counter(
            "risk_circuit_breaker_trips_total",
            "Circuit breaker trips",
            ["breaker_name"],
            registry=self.registry,
        )

        self.circuit_breaker_state = Gauge(
            "risk_circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=tripped)",
            ["breaker_name"],
            registry=self.registry,
        )

        self.liquidation_events_total = Counter(
            "risk_liquidation_events_total",
            "Total liquidation events",
            ["symbol"],
            registry=self.registry,
        )

        self.liquidity_cap_hits_total = Counter(
            "risk_liquidity_cap_hits_total",
            "Times liquidity cap was hit",
            ["symbol"],
            registry=self.registry,
        )

        # ------------------------------------------------------------------ #
        # Model Metrics
        # ------------------------------------------------------------------ #

        self.model_inference_seconds = Histogram(
            "model_inference_seconds",
            "Model inference time",
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5],
            registry=self.registry,
        )

        self.model_prediction = Gauge(
            "model_prediction",
            "Latest model prediction",
            ["output_name"],
            registry=self.registry,
        )

        self.model_load_time_seconds = Gauge(
            "model_load_time_seconds",
            "Time to load model",
            registry=self.registry,
        )

        # ------------------------------------------------------------------ #
        # WebSocket Metrics
        # ------------------------------------------------------------------ #

        self.websocket_messages_total = Counter(
            "websocket_messages_total",
            "Total WebSocket messages received",
            ["channel"],
            registry=self.registry,
        )

        self.websocket_reconnects_total = Counter(
            "websocket_reconnects_total",
            "Total WebSocket reconnections",
            registry=self.registry,
        )

        self.websocket_latency_ms = Histogram(
            "websocket_latency_ms",
            "WebSocket message latency",
            ["channel"],
            buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000],
            registry=self.registry,
        )

    def export(self) -> bytes:
        """Export metrics in Prometheus format."""
        return generate_latest(self.registry)

    def get_content_type(self) -> str:
        """Get content type for metrics endpoint."""
        return CONTENT_TYPE_LATEST

    # ------------------------------------------------------------------ #
    # Helper methods for common metric updates
    # ------------------------------------------------------------------ #

    def record_order(self, symbol: str, side: str, order_type: str, latency: float):
        """Record order placement."""
        self.orders_total.labels(symbol=symbol, side=side, order_type=order_type).inc()
        self.order_latency_seconds.labels(symbol=symbol).observe(latency)

    def record_fill(self, symbol: str, side: str, latency: float):
        """Record order fill."""
        self.fills_total.labels(symbol=symbol, side=side).inc()
        self.fill_latency_seconds.labels(symbol=symbol).observe(latency)

    def record_rejection(self, symbol: str, side: str, reason: str):
        """Record order rejection."""
        self.orders_rejected_total.labels(
            symbol=symbol, side=side, rejection_reason=reason
        ).inc()

    def update_position(self, symbol: str, size: float, value_usd: float):
        """Update position metrics."""
        self.position_size.labels(symbol=symbol).set(size)
        self.position_value_usd.labels(symbol=symbol).set(value_usd)

    def update_portfolio(
        self, value_usd: float, realized_pnl: float, unrealized_pnl: float
    ):
        """Update portfolio metrics."""
        self.portfolio_value_usd.set(value_usd)
        self.pnl_realized_usd.set(realized_pnl)
        self.pnl_unrealized_usd.set(unrealized_pnl)

    def record_trading_cost(self, cost_type: str, amount_usd: float):
        """Record trading cost."""
        self.trading_costs_usd.labels(cost_type=cost_type).inc(amount_usd)

    def record_api_request(
        self,
        endpoint: str,
        method: str,
        status: int,
        latency: float,
        error: Optional[str] = None,
    ):
        """Record API request."""
        self.api_requests_total.labels(
            endpoint=endpoint, method=method, status=str(status)
        ).inc()
        self.api_latency_seconds.labels(endpoint=endpoint).observe(latency)

        if error:
            self.api_errors_total.labels(endpoint=endpoint, error_type=error).inc()

    def record_circuit_breaker_trip(self, breaker_name: str):
        """Record circuit breaker trip."""
        self.circuit_breaker_trips_total.labels(breaker_name=breaker_name).inc()
        self.circuit_breaker_state.labels(breaker_name=breaker_name).set(1)

    def record_circuit_breaker_reset(self, breaker_name: str):
        """Record circuit breaker reset."""
        self.circuit_breaker_state.labels(breaker_name=breaker_name).set(0)

    def update_risk_metrics(self, margin_ratio: float, leverage: float):
        """Update risk metrics."""
        self.margin_ratio.set(margin_ratio)
        self.leverage.set(leverage)

    def update_system_metrics(self, memory_bytes: float, cpu_percent: float):
        """Update system metrics."""
        self.process_memory_bytes.set(memory_bytes)
        self.cpu_usage_percent.set(cpu_percent)


# Global instance with thread-safe initialization (CRITICAL FIX: race condition)
_metrics: Optional[TradingMetrics] = None
_metrics_lock = threading.Lock()


def get_metrics() -> TradingMetrics:
    """
    Get or create global metrics instance.

    Uses double-checked locking pattern for thread-safe lazy initialization.
    """
    global _metrics
    if _metrics is None:
        with _metrics_lock:
            # Check again inside lock (double-checked locking)
            if _metrics is None:
                _metrics = TradingMetrics()
    return _metrics


def reset_metrics():
    """Reset global metrics (for testing)."""
    global _metrics
    with _metrics_lock:
        _metrics = None
