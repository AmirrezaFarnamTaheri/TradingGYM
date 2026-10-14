"""Durable control-file helpers shared by operator and trading processes."""

from __future__ import annotations

import hashlib
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


def write_heartbeat(
    path: Path = HEARTBEAT_FILE, *, timestamp: float | None = None
) -> float:
    value = time.time() if timestamp is None else float(timestamp)
    atomic_write_text(
        path, f"{value:.6f}\n", max_bytes=MAX_CONTROL_FILE_BYTES, mode=0o600
    )
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


# Identity of one specific committed panic request. An atomic replacement with
# identical content still has a different inode, preventing an older worker
# from acknowledging a newer, independently written emergency instruction.
PanicIdentity = tuple[int, int, int, int, bytes]


def _panic_identity_unlocked(path: Path) -> PanicIdentity | None:
    """Capture one bounded signal while the caller holds its file lock."""

    if path.is_symlink():
        raise ValueError("panic signal is invalid")
    if not path.exists():
        return None
    stat = path.stat()
    if not path.is_file() or not (0 < stat.st_size <= MAX_CONTROL_FILE_BYTES):
        raise ValueError("panic signal is invalid")
    with path.open("rb") as stream:
        payload = stream.read(MAX_CONTROL_FILE_BYTES + 1)
    if len(payload) != stat.st_size:
        raise ValueError("panic signal changed during read")
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_mtime_ns,
        stat.st_size,
        hashlib.sha256(payload).digest(),
    )


def clear_panic_signal(
    path: Path = PANIC_FILE, *, expected_identity: PanicIdentity
) -> bool:
    """Acknowledge only the exact request already processed and verified.

    Both panic writes (atomic_write_text) and identity comparisons hold the
    same file lock. Do not hold the lock while waiting on the broker; if a
    new panic arrives during flattening, leave that new signal for the
    live worker to process rather than silently deleting it.
    """

    with FileLock(str(path) + ".lock", timeout=5):
        current = _panic_identity_unlocked(path)
        if current is None or current != expected_identity:
            return False
        path.unlink()
        return True


async def handle_panic_request(adapter: object | None, path: Path = PANIC_FILE) -> bool:
    """Confirm a gross-flat result before clearing this exact panic request."""

    if not panic_requested(path):
        return False
    # Paper/observer workers share the runtime directory with the live
    # executor. Without an adapter they cannot verify broker flatness and
    # must not erase the global emergency signal.
    if adapter is None:
        return False

    with FileLock(str(path) + ".lock", timeout=5):
        expected_identity = _panic_identity_unlocked(path)
    if expected_identity is None:
        return False

    flatten = getattr(adapter, "flatten", None)
    if flatten is None:
        raise TypeError("panic adapter does not provide flatten()")
    if not bool(await flatten()):
        return False
    return clear_panic_signal(path, expected_identity=expected_identity)
