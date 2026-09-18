"""Configurable non-blocking sliding-window rate limits.

The limiter deliberately rejects excess work instead of sleeping while holding a
worker or request slot. Callers can retry with the reported delay or fail over to
another provider.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


class RateLimitExceeded(RuntimeError):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(0.0, retry_after)
        super().__init__(f"rate limit exceeded; retry after {self.retry_after:.3f}s")


@dataclass(frozen=True)
class RateLimitSnapshot:
    used: int
    capacity: int
    window_seconds: float
    retry_after: float


class SlidingWindowLimiter:
    def __init__(self, *, capacity: int, window_seconds: float) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.capacity = capacity
        self.window_seconds = window_seconds
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        boundary = now - self.window_seconds
        while self._events and self._events[0] <= boundary:
            self._events.popleft()

    def acquire(self, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        with self._lock:
            self._prune(current)
            if len(self._events) >= self.capacity:
                retry_after = self.window_seconds - (current - self._events[0])
                raise RateLimitExceeded(retry_after)
            self._events.append(current)

    def snapshot(self, *, now: float | None = None) -> RateLimitSnapshot:
        current = time.monotonic() if now is None else now
        with self._lock:
            self._prune(current)
            retry_after = 0.0
            if len(self._events) >= self.capacity:
                retry_after = max(
                    0.0, self.window_seconds - (current - self._events[0])
                )
            return RateLimitSnapshot(
                len(self._events), self.capacity, self.window_seconds, retry_after
            )
