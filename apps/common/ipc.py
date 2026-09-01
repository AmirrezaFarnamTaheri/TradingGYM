"""Crash-safe, bounded metrics IPC for the API and trading workers.

The original fixed mmap format could expose partially-written JSON after a
worker crash and silently swallowed lock/contention failures.  The current
format uses an atomic JSON envelope with a checksum.  Readers retain support
for the legacy mmap layout to allow rolling upgrades.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mmap
import os
import struct
import time
from pathlib import Path
from typing import Any

from trading_gym.common.persistence import atomic_write_json, read_json
from trading_gym.common.security import bounded_json_dumps, redact_secrets

logger = logging.getLogger(__name__)

DEFAULT_MMAP_SIZE = 1024 * 100
DEFAULT_MMAP_FILE = "shared_metrics.dat"
FORMAT_VERSION = 2
MAX_METRICS_BYTES = 96 * 1024
MAX_METRICS_AGE_SECONDS = 300.0


def _payload_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class MetricsWriter:
    """Atomically publish one bounded, checksummed metrics snapshot."""

    def __init__(self, filename: str | Path = DEFAULT_MMAP_FILE, size: int = DEFAULT_MMAP_SIZE):
        self.filename = Path(filename)
        self.size = max(256, int(size))
        self.filename.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._sequence = 0

    def write(self, data: dict[str, Any]) -> bool:
        if not isinstance(data, dict):
            raise TypeError("metrics payload must be a dictionary")
        payload = redact_secrets(data)
        bounded_json_dumps(payload, max_bytes=min(self.size - 128, MAX_METRICS_BYTES))
        self._sequence += 1
        envelope = {
            "format": "trading-gym-metrics",
            "version": FORMAT_VERSION,
            "sequence": self._sequence,
            "written_at": time.time(),
            "payload_sha256": _payload_digest(payload),
            "payload": payload,
        }
        atomic_write_json(self.filename, envelope)
        return True

    def close(self) -> None:
        """Kept for API compatibility; writes use short-lived file handles."""


class MetricsReader:
    """Read and validate the newest complete metrics snapshot."""

    def __init__(
        self,
        filename: str | Path = DEFAULT_MMAP_FILE,
        size: int = DEFAULT_MMAP_SIZE,
        *,
        max_age_seconds: float | None = MAX_METRICS_AGE_SECONDS,
    ):
        self.filename = Path(filename)
        self.size = max(256, int(size))
        self.max_age_seconds = max_age_seconds
        self.last_error: str | None = None

    def read(self) -> dict[str, Any]:
        if not self.filename.exists() or self.filename.is_symlink():
            return {}
        try:
            if self._looks_like_json():
                return self._read_envelope()
            return self._read_legacy_mmap()
        except (OSError, ValueError, TypeError, json.JSONDecodeError, struct.error) as exc:
            self.last_error = type(exc).__name__
            logger.warning("Ignoring invalid metrics snapshot error_type=%s", type(exc).__name__)
            return {}

    def _looks_like_json(self) -> bool:
        with self.filename.open("rb") as handle:
            return handle.read(1) in {b"{", b"["}

    def _read_envelope(self) -> dict[str, Any]:
        envelope = read_json(self.filename, default={})
        if not isinstance(envelope, dict):
            raise ValueError("metrics envelope must be an object")
        if envelope.get("format") != "trading-gym-metrics" or envelope.get("version") != FORMAT_VERSION:
            raise ValueError("unsupported metrics envelope")
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("metrics payload must be an object")
        if envelope.get("payload_sha256") != _payload_digest(payload):
            raise ValueError("metrics checksum mismatch")
        written_at = float(envelope.get("written_at", 0.0))
        if written_at <= 0:
            raise ValueError("metrics timestamp is invalid")
        if self.max_age_seconds is not None and time.time() - written_at > self.max_age_seconds:
            self.last_error = "stale"
            return {}
        self.last_error = None
        return payload

    def _read_legacy_mmap(self) -> dict[str, Any]:
        """Read the v1 mmap layout during rolling upgrades."""

        stat_result = self.filename.stat()
        if stat_result.st_size < 4 or stat_result.st_size > max(self.size, DEFAULT_MMAP_SIZE) * 4:
            raise ValueError("legacy metrics file has invalid size")
        map_size = min(stat_result.st_size, self.size)
        with self.filename.open("rb") as handle:
            with mmap.mmap(handle.fileno(), map_size, access=mmap.ACCESS_READ) as mm:
                length = struct.unpack(">I", mm.read(4))[0]
                if length == 0 or length > map_size - 4 or length > MAX_METRICS_BYTES:
                    raise ValueError("legacy metrics payload length is invalid")
                payload = json.loads(mm.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("legacy metrics payload must be an object")
        self.last_error = None
        return payload
