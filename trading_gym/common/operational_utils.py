"""Fill reconciliation tolerance and a Retry-After-aware token bucket.

Two small operational absorptions:
- QuantDinger's final-fill tolerance reconciliation (`sent_order_recovery.py:8-40`):
  expected vs reported fills rarely match to the last satoshi; reconcile with an
  explicit tolerance and surface the delta instead of failing binary.
- longbridge `openapi/rate_limiter.rs:112-170`: token bucket that prefers the
  server's ``Retry-After`` hint over its own computed wait.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class FillReconciliation:
    matched: bool
    delta: float
    tolerance: float

    def to_dict(self) -> dict:
        return {"matched": self.matched, "delta": str(self.delta),
                "tolerance": str(self.tolerance)}


def reconcile_fill(
    *,
    expected_quantity: float,
    filled_quantity: float,
    tolerance_fraction: float = 0.001,
) -> FillReconciliation:
    """Match when |delta| <= tolerance × max(expected, tiny)."""
    if tolerance_fraction < 0:
        raise ValueError("tolerance_fraction must be >= 0")
    if not all(
        np_isfinite(v) for v in (expected_quantity, filled_quantity)
    ):
        raise ValueError("quantities must be finite")
    base = max(abs(expected_quantity), 1e-12)
    delta = filled_quantity - expected_quantity
    matched = abs(delta) <= tolerance_fraction * base
    return FillReconciliation(matched=matched, delta=delta, tolerance=tolerance_fraction)


def np_isfinite(value: float) -> bool:
    return value == value and -1e308 < value < 1e308  # NaN/inf guard without numpy


class TokenBucketLimiter:
    """Token bucket with server-hint override (``Retry-After`` seconds).

    ``acquire()`` consumes one token or sleeps; :meth:`penalize` applies a
    server-provided minimum-wait that overrides the natural refill window
    until it elapses.
    """

    def __init__(self, *, rate_per_second: float, burst: int = 1):
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if burst < 1:
            raise ValueError("burst must be >= 1")
        self.rate = float(rate_per_second)
        self.capacity = int(burst)
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._retry_after_until = 0.0

    def penalize(self, retry_after_seconds: float) -> None:
        """Server told us to back off: honour at least that long."""
        if retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be >= 0")
        until = time.monotonic() + retry_after_seconds
        self._retry_after_until = max(self._retry_after_until, until)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        self._tokens = min(float(self.capacity), self._tokens + elapsed * self.rate)
        self._last = now

    def acquire(self, *, sleep=time.sleep) -> float:
        """Consume one token; returns seconds waited (0 when immediate)."""
        waited = 0.0
        while True:
            self._refill()
            now = time.monotonic()
            if self._tokens >= 1.0 and now >= self._retry_after_until:
                self._tokens -= 1.0
                return waited
            wait_natural = (
                (1.0 - self._tokens) / self.rate if self._tokens < 1.0 else 0.0
            )
            wait_penalty = max(0.0, self._retry_after_until - now)
            step = max(wait_natural, wait_penalty, 0.005)
            sleep(step)
            waited += step
