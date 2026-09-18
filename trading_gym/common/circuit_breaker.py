"""Thread-safe bounded circuit breaker for external data and model providers."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitSnapshot:
    state: CircuitState
    failures: int
    opened_at: float | None


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(
        self, *, failure_threshold: int = 5, recovery_timeout: float = 30.0
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be positive")
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False
        self._lock = threading.Lock()

    def snapshot(self) -> CircuitSnapshot:
        with self._lock:
            return CircuitSnapshot(
                self._state_unlocked(time.monotonic()), self._failures, self._opened_at
            )

    def _state_unlocked(self, now: float) -> CircuitState:
        if self._opened_at is None:
            return CircuitState.CLOSED
        if now - self._opened_at >= self.recovery_timeout:
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def call(self, operation: Callable[[], T]) -> T:
        with self._lock:
            state = self._state_unlocked(time.monotonic())
            if state is CircuitState.OPEN:
                raise CircuitOpenError("provider circuit is open")
            if state is CircuitState.HALF_OPEN:
                if self._half_open_in_flight:
                    raise CircuitOpenError("provider circuit is probing recovery")
                self._half_open_in_flight = True
        try:
            value = operation()
        except Exception:
            with self._lock:
                self._failures += 1
                self._half_open_in_flight = False
                if self._failures >= self.failure_threshold:
                    self._opened_at = time.monotonic()
            raise
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_in_flight = False
        return value
