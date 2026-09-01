"""Durable control-file helpers shared by operator and trading processes."""

from __future__ import annotations

import time
from pathlib import Path

from filelock import FileLock

from trading_gym.common.persistence import atomic_write_text, resolve_data_home

RUNTIME_ROOT = resolve_data_home() / "runtime"
if RUNTIME_ROOT.exists() and RUNTIME_ROOT.is_symlink():
    raise ValueError("runtime control directory must not be a symbolic link")
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
PANIC_FILE = RUNTIME_ROOT / "panic.signal"
HEARTBEAT_FILE = RUNTIME_ROOT / "health.heartbeat"
METRICS_FILE = RUNTIME_ROOT / "shared_metrics.dat"
MAX_CONTROL_FILE_BYTES = 512


def write_heartbeat(path: Path = HEARTBEAT_FILE, *, timestamp: float | None = None) -> float:
    value = time.time() if timestamp is None else float(timestamp)
    atomic_write_text(path, f"{value:.6f}\n", max_bytes=MAX_CONTROL_FILE_BYTES, mode=0o600)
    return value


def panic_requested(path: Path = PANIC_FILE) -> bool:
    """Fail closed: any present or unreadable panic path requests a halt."""

    try:
        if not path.exists() and not path.is_symlink():
            return False
        if path.is_symlink():
            return True
        # Presence is authoritative. Oversized or empty files are malformed but
        # must not allow trading to continue.
        path.stat()
        return True
    except OSError:
        return True


def clear_panic_signal(path: Path = PANIC_FILE) -> bool:
    """Remove a committed panic signal under a process-safe lock.

    Call this only after the caller has completed the safety action.  A failed
    flatten therefore leaves the signal in place for the next retry/restart.
    """

    with FileLock(str(path) + ".lock", timeout=5):
        if not path.exists():
            return False
        if path.is_symlink() or path.stat().st_size > MAX_CONTROL_FILE_BYTES:
            raise ValueError("panic signal is invalid")
        path.unlink()
        return True

async def handle_panic_request(adapter: object | None, path: Path = PANIC_FILE) -> bool:
    """Flatten when required and clear the signal only after confirmed success."""

    if not panic_requested(path):
        return False
    flattened = True
    if adapter is not None:
        flatten = getattr(adapter, "flatten", None)
        if flatten is None:
            raise TypeError("panic adapter does not provide flatten()")
        flattened = bool(await flatten())
    if not flattened:
        return False
    clear_panic_signal(path)
    return True

