"""Small, crash-safe persistence helpers for local single-node services.

The convergence planes deliberately use simple local files so that they remain
portable and inspectable.  These helpers provide the durability and confinement
properties those stores rely on: bounded reads, symlink rejection, private
permissions, atomic replacement, file and directory fsync, and locked appends.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock


MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_JSONL_RECORD_BYTES = 1024 * 1024


def _fsync_directory(path: Path) -> None:
    """Best-effort directory fsync after create/replace operations.

    POSIX filesystems need the containing directory to be synced for a rename to
    survive sudden power loss.  Windows does not expose the same portable
    primitive, so unsupported platforms fail harmlessly after file fsync.
    """

    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing to use symlink-backed state file: {path}")


def resolve_data_home() -> Path:
    """Return the configured durable data directory and create it privately."""

    configured = os.getenv("TRADING_GYM_HOME")
    root = Path(configured).expanduser() if configured else Path.home() / ".trading-gym"
    if root.exists() and root.is_symlink():
        raise ValueError("TRADING_GYM_HOME must not be a symbolic link")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not root.is_dir():
        raise ValueError(f"TRADING_GYM_HOME is not a directory: {root}")
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def read_json(path: Path, default: Any, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    _reject_symlink(path)
    with FileLock(str(path) + ".lock"):
        try:
            size = path.stat().st_size
            if size > max_bytes:
                raise ValueError(f"JSON document exceeds {max_bytes} byte limit: {path}")
            raw = path.read_bytes()
            if len(raw) > max_bytes:
                raise ValueError(f"JSON document exceeds {max_bytes} byte limit: {path}")
        except OSError as exc:
            raise ValueError(f"Unable to read valid JSON from {path}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to read valid JSON from {path}") from exc


def atomic_write_json(path: Path, value: Any) -> None:
    """Write JSON through an fsync'd sibling temporary file and atomic replace."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    serialised = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(serialised.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ValueError(f"JSON document exceeds {MAX_DOCUMENT_BYTES} byte limit")
    atomic_write_text(path, serialised)


def atomic_write_text(
    path: Path,
    value: str,
    *,
    max_bytes: int = MAX_DOCUMENT_BYTES,
    mode: int = 0o600,
) -> None:
    """Atomically replace a UTF-8 text file and persist the rename."""

    encoded = value.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(f"Text document exceeds {max_bytes} byte limit")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with FileLock(str(path) + ".lock"):
        if path.exists():
            _reject_symlink(path)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                temp_path.chmod(mode)
            except OSError:
                pass
            os.replace(temp_path, path)
            try:
                path.chmod(mode)
            except OSError:
                pass
            _fsync_directory(path.parent)
        finally:
            if temp_path.exists():
                temp_path.unlink()


def append_jsonl(path: Path, value: Any) -> None:
    """Append one durable JSON line under an inter-process lock."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if len(line.encode("utf-8")) > MAX_JSONL_RECORD_BYTES:
        raise ValueError(f"JSONL record exceeds {MAX_JSONL_RECORD_BYTES} byte limit")
    with FileLock(str(path) + ".lock"):
        if path.exists():
            _reject_symlink(path)
        existed = path.exists()
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            path.chmod(0o600)
        except OSError:
            pass
        if not existed:
            _fsync_directory(path.parent)
