"""Bounded, process-shared request admission for advisory provider calls.

Every upstream HTTP attempt consumes one slot *before* transmission, even if
it times out or returns an error. A cap is scoped to a provider ID and base URL
(not to a key, model, or process); key rotation cannot reset the counter.
This is a request-count ceiling, NOT a token, monetary or provider-side quota.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Callable

from filelock import FileLock, Timeout

_MAX_REQUESTS = 100_000
_MAX_STATE_BYTES = 512
_SECONDS_PER_WINDOW = 3600


class RequestBudgetUnavailable(RuntimeError):
    """Budget evidence cannot be trusted; deny further provider requests."""


class RequestBudgetExhausted(RequestBudgetUnavailable):
    """The current UTC-hour request admission limit has been reached."""


class ProviderRequestBudget:
    """Reserve attempts atomically across workers sharing the data home.

    A lower cap observed during a window cannot be undone by a worker still
    using older configuration. The shared state is not tamper-proof against
    an operator with write access to the configured data directory.
    """

    def __init__(
        self,
        state_path: Path,
        *,
        limit: int,
        now: Callable[[], float] = time.time,
    ) -> None:
        if type(limit) is not int or not 1 <= limit <= _MAX_REQUESTS:  # noqa: E721
            raise ValueError("provider request cap must be a positive bounded integer")
        self.state_path = Path(state_path)
        self.limit = limit
        self._now = now

    def _window(self) -> int:
        stamp = self._now()
        if (
            type(stamp) not in (int, float)
            or not math.isfinite(stamp)
            or stamp < 0
            or stamp > 9_999_999_999
        ):
            raise RequestBudgetUnavailable("provider budget clock is invalid")
        return (int(stamp) // _SECONDS_PER_WINDOW) * _SECONDS_PER_WINDOW

    def _lock(self) -> FileLock:
        path = self.state_path
        if path.parent.is_symlink():
            raise RequestBudgetUnavailable("provider budget directory is unsafe")
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if path.is_symlink() or Path(str(path) + ".lock").is_symlink():
                raise RequestBudgetUnavailable("provider budget state is unsafe")
        except OSError as exc:
            raise RequestBudgetUnavailable(
                "provider budget storage unavailable"
            ) from exc
        return FileLock(str(path) + ".lock", timeout=2)

    def _read(self, window: int) -> dict[str, int]:
        path = self.state_path
        try:
            if path.is_symlink():
                raise ValueError("symlink")
            if not path.exists():
                return {
                    "schema_version": 1,
                    "window_start": window,
                    "used": 0,
                    "ceiling": self.limit,
                }
            metadata = path.stat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > _MAX_STATE_BYTES
            ):
                raise ValueError("unexpected file")
            state = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(state, dict)
                or set(state) != {"schema_version", "window_start", "used", "ceiling"}
                or any(type(state[key]) is not int for key in state)  # noqa: E721
                or state["schema_version"] != 1
                or state["window_start"] < 0
                or state["window_start"] % _SECONDS_PER_WINDOW
                or not 0 <= state["used"] <= _MAX_REQUESTS
                or not 1 <= state["ceiling"] <= _MAX_REQUESTS
            ):
                raise ValueError("invalid schema")
            if window < state["window_start"]:
                raise RequestBudgetUnavailable("provider budget clock moved backwards")
            return state
        except RequestBudgetUnavailable:
            raise
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise RequestBudgetUnavailable("provider budget state is invalid") from exc

    def _write(self, state: dict[str, int]) -> None:
        payload = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        if len(payload) > _MAX_STATE_BYTES:
            raise RequestBudgetUnavailable("provider budget state is too large")
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(
                prefix=".provider-budget-", dir=self.state_path.parent
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            if self.state_path.is_symlink():
                raise RequestBudgetUnavailable("provider budget state is unsafe")
            os.replace(temporary, self.state_path)
            temporary = None
        except OSError as exc:
            raise RequestBudgetUnavailable("provider budget state unwritable") from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def reserve(self) -> None:
        """Consume one call slot or raise before any upstream network attempt."""
        window = self._window()
        try:
            with self._lock():
                state = self._read(window)
                if state["window_start"] < window:
                    state = {
                        "schema_version": 1,
                        "window_start": window,
                        "used": 0,
                        "ceiling": self.limit,
                    }
                else:
                    state["ceiling"] = min(state["ceiling"], self.limit)
                if state["used"] >= state["ceiling"]:
                    # Persist a newly lowered cap so an old worker cannot
                    # ignore it by continuing to use its higher local value.
                    self._write(state)
                    raise RequestBudgetExhausted(
                        "provider hourly request cap exhausted"
                    )
                state["used"] += 1
                self._write(state)
        except Timeout as exc:
            raise RequestBudgetUnavailable("provider budget lock unavailable") from exc

    def status(self) -> dict[str, int]:
        """Read non-secret counts; never change the admission ledger."""
        window = self._window()
        try:
            with self._lock():
                state = self._read(window)
                if state["window_start"] < window:
                    state = {
                        "schema_version": 1,
                        "window_start": window,
                        "used": 0,
                        "ceiling": self.limit,
                    }
                effective = min(self.limit, state["ceiling"])
                return {
                    "window_start": state["window_start"],
                    "used": state["used"],
                    "ceiling": effective,
                    "remaining": max(0, effective - state["used"]),
                }
        except Timeout as exc:
            raise RequestBudgetUnavailable("provider budget lock unavailable") from exc


def default_request_budget_path(provider_id: str, base_url: str) -> Path:
    """Stable across key additions and models for a single configured route."""
    from trading_gym.core.persistence import resolve_data_home

    digest = hashlib.sha256(f"{provider_id}\0{base_url}".encode()).hexdigest()[:24]
    return resolve_data_home() / "intelligence" / f"requests-{digest}.json"
