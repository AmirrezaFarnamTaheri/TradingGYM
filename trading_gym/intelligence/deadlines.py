"""One monotonic admission deadline across a synchronous decision cascade.

A late result is rejected and no later provider is invoked. This is not CPU,
DNS or native-library preemption: use an isolated worker for a hard wall-time
kill. HTTP operations also receive the remaining budget as their timeout.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import math
import time
from typing import Callable, Iterator
from trading_gym.intelligence.types import BackendUnavailable


class DecisionDeadlineExceeded(BackendUnavailable):
    """No action may be admitted from this expired cycle."""


@dataclass(frozen=True)
class _Deadline:
    ends_at: float
    clock: Callable[[], float]

    def remaining(self) -> float:
        left = self.ends_at - self.clock()
        if left <= 0:
            raise DecisionDeadlineExceeded("decision_cycle_deadline_exceeded")
        return left

_CURRENT: ContextVar[_Deadline | None] = ContextVar("intelligence_deadline", default=None)


@contextmanager
def decision_budget(seconds: float, *, clock: Callable[[], float] = time.monotonic) -> Iterator[None]:
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 120:
        raise ValueError("decision budget must be finite within (0,120] seconds")
    outer = _CURRENT.get()
    if outer is not None:
        # Different clocks cannot be compared by epoch; bound the duration.
        seconds = min(seconds, outer.remaining())
        clock = outer.clock
    ends_at = clock() + seconds
    if outer is not None: ends_at = min(ends_at, outer.ends_at)
    token = _CURRENT.set(_Deadline(ends_at, clock))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def remaining_seconds(maximum: float) -> float:
    deadline = _CURRENT.get()
    return min(maximum, deadline.remaining()) if deadline is not None else maximum


def check_deadline() -> None:
    deadline = _CURRENT.get()
    if deadline is not None: deadline.remaining()
