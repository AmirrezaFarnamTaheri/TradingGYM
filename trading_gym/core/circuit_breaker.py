"""Thread-safe bounded circuit breaker for external data and model providers."""

from __future__ import annotations

import math
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
    """Generation-fenced circuit for overlapping requests.

    An operation admitted before a circuit transition may return later. Its
    result is still returned to that caller, but it cannot overwrite newer
    OPEN or recovered CLOSED state. Only the admitted HALF_OPEN probe can
    establish recovery after cooldown.
    """

    def __init__(
        self, *, failure_threshold: int = 5, recovery_timeout: float = 30.0
    ) -> None:
        if (
            not isinstance(failure_threshold, int)
            or isinstance(failure_threshold, bool)
            or failure_threshold < 1
        ):
            raise ValueError("failure_threshold must be a positive integer")
        if (
            isinstance(recovery_timeout, bool)
            or not isinstance(recovery_timeout, (int, float))
            or not math.isfinite(recovery_timeout)
            or recovery_timeout <= 0
        ):
            raise ValueError("recovery_timeout must be finite and positive")
        self.failure_threshold = failure_threshold
        self.recovery_timeout = float(recovery_timeout)
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False
        self._generation = 0
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
            is_probe = state is CircuitState.HALF_OPEN
            if is_probe:
                if self._half_open_in_flight:
                    raise CircuitOpenError("provider circuit is probing recovery")
                self._half_open_in_flight = True
            generation = self._generation
        try:
            value = operation()
        except Exception:
            with self._lock:
                if generation == self._generation:
                    self._failures += 1
                    if is_probe:
                        self._half_open_in_flight = False
                    if is_probe or self._failures >= self.failure_threshold:
                        self._opened_at = time.monotonic()
                        self._generation += 1
            raise
        with self._lock:
            if generation == self._generation:
                self._failures = 0
                self._opened_at = None
                if is_probe:
                    self._half_open_in_flight = False
                    self._generation += 1
        return value
