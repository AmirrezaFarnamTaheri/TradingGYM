"""Security and reliability primitives for the operator API runtime."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

import psutil

WS_PROTOCOL = "trading-gym.v1"
WS_AUTH_PREFIX = "auth."
MAX_WS_TOKEN_BYTES = 4096
MAX_LOG_MESSAGE_CHARS = 16_384
MAX_PUBLIC_ERROR_CHARS = 512

CONTROL_PLANE_SECRET_ENV = frozenset(
    {
        "API_KEY",
        "WORKER_API_KEY",
        "APPROVAL_API_KEY",
        "AUDIT_HMAC_KEY",
        "AUDIT_HMAC_KEYRING_JSON",
        "ACTOR_CREDENTIALS_JSON",
        "VITE_API_KEY",
    }
)

_SECRET_ENV_SUFFIXES = (
    "API_KEY",
    "_TOKEN",
    "TOKEN",
    "_SECRET",
    "SECRET",
    "_PASSWORD",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET_ACCESS_KEY",
)

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<name>
        [A-Za-z0-9_.-]*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|
        auth(?:orization)?|password|passwd|secret|private[_-]?key)
    )
    (?P<separator>\s*[:=]\s*)
    (?P<quote>["']?)
    (?P<value>[^\s,;&"']+)
    (?P=quote)
    """
)
_BEARER_SECRET = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{4,}")
_URL_USERINFO = re.compile(r"(?i)(https?://)([^/@\s]+)@")


def child_environment(
    *,
    allowed_control_secrets: Iterable[str] = (),
    allowed_secret_env: Iterable[str] = (),
) -> dict[str, str]:
    """Build a least-privilege worker environment.

    Control-plane credentials are always removed unless explicitly delegated.
    Other credential-like variables are removed by default and must be named by
    the caller, preventing training, paper, and utility workers from inheriting
    unrelated broker, provider, or deployment secrets.
    """

    allowed_control = set(allowed_control_secrets)
    allowed_secrets = set(allowed_secret_env)
    environment = os.environ.copy()
    for name in list(environment):
        if name in CONTROL_PLANE_SECRET_ENV and name not in allowed_control:
            environment.pop(name, None)
            continue
        upper = name.upper()
        credential_like = any(upper.endswith(suffix) for suffix in _SECRET_ENV_SUFFIXES)
        if credential_like and name not in allowed_secrets and name not in allowed_control:
            environment.pop(name, None)
    return environment


def command_digest(command: Iterable[str]) -> str:
    encoded = "\0".join(command).encode("utf-8", errors="surrogateescape")
    return hashlib.sha256(encoded).hexdigest()


def capture_process_identity(pid: int, command: Iterable[str]) -> dict[str, Any]:
    process = psutil.Process(pid)
    actual_command: list[str] = []
    # A freshly forked child can briefly expose an empty cmdline before exec.
    # Bound the retry so startup cannot hang while still capturing kernel truth.
    for _ in range(25):
        actual_command = process.cmdline()
        if actual_command:
            break
        if not process.is_running():
            break
        time.sleep(0.01)
    if not actual_command:
        raise ProcessLookupError("process command line is unavailable")
    return {
        "pid": pid,
        "create_time": process.create_time(),
        # Hash the kernel-observed command, not the caller's requested argv.
        # Launchers and operating systems may canonicalise the executable path.
        "command_sha256": command_digest(actual_command),
        "requested_command_sha256": command_digest(command),
    }


def process_identity_matches(record: dict[str, Any]) -> bool:
    """Return true only when a PID still represents the originally spawned process."""

    try:
        process = psutil.Process(int(record["pid"]))
        expected_created = float(record["create_time"])
        if abs(process.create_time() - expected_created) > 0.01:
            return False
        expected_digest = record.get("command_sha256")
        if expected_digest:
            current = command_digest(process.cmdline())
            if current != expected_digest:
                return False
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (KeyError, TypeError, ValueError, psutil.Error):
        return False


def terminate_process_tree(
    record: dict[str, Any],
    *,
    graceful_timeout: float = 10.0,
) -> dict[str, int]:
    """Terminate a verified process tree without acting on a reused PID."""

    if not process_identity_matches(record):
        raise ProcessLookupError("managed process identity no longer matches")
    parent = psutil.Process(int(record["pid"]))
    children = parent.children(recursive=True)
    targets = children + [parent]
    for process in targets:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    gone, alive = psutil.wait_procs(targets, timeout=graceful_timeout)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    if alive:
        killed, still_alive = psutil.wait_procs(alive, timeout=3.0)
        gone.extend(killed)
        alive = still_alive
    return {"terminated": len(gone), "remaining": len(alive)}


def decode_websocket_auth_protocol(header: str | None) -> tuple[str | None, str | None]:
    """Extract a base64url API key from ``Sec-WebSocket-Protocol`` tokens."""

    if not header:
        return None, None
    tokens = [item.strip() for item in header.split(",") if item.strip()]
    selected = WS_PROTOCOL if WS_PROTOCOL in tokens else None
    encoded = next((item[len(WS_AUTH_PREFIX) :] for item in tokens if item.startswith(WS_AUTH_PREFIX)), None)
    if encoded is None:
        return None, selected
    if not encoded or len(encoded) > MAX_WS_TOKEN_BYTES * 2:
        return None, selected
    padding = "=" * (-len(encoded) % 4)
    try:
        raw = base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
        if len(raw) > MAX_WS_TOKEN_BYTES:
            return None, selected
        token = raw.decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError, binascii.Error, ValueError):
        return None, selected
    return token, selected


def redact_log_text(message: str) -> str:
    """Return bounded operator-safe text with common credential forms removed.

    Child processes and remote libraries can emit arbitrary strings. This is a
    final presentation boundary, not a replacement for least-privilege child
    environments: it strips terminal controls, user-info URLs, bearer tokens,
    and assignment-style secret fields before logs reach memory, consoles, or
    WebSocket clients.
    """

    message = _ANSI_ESCAPE.sub("", str(message))
    message = "".join(
        character if character in {"\t", "\n"} or ord(character) >= 32 else "�"
        for character in message
    )
    message = _URL_USERINFO.sub(r"\1[REDACTED]@", message)
    message = _BEARER_SECRET.sub("Bearer [REDACTED]", message)

    def replace_assignment(match: re.Match[str]) -> str:
        return f"{match.group('name')}{match.group('separator')}[REDACTED]"

    message = _SECRET_ASSIGNMENT.sub(replace_assignment, message)
    if len(message) > MAX_LOG_MESSAGE_CHARS:
        message = message[: MAX_LOG_MESSAGE_CHARS - 14] + "…[truncated]"
    return message


def public_error_detail(
    error: BaseException,
    *,
    fallback: str = "Request could not be completed",
) -> str:
    """Return bounded, single-line, credential-redacted domain error text.

    Callers must use this only after classifying an exception as a safe
    validation, conflict, or degraded-service condition. Unexpected/system
    failures must continue to return a fixed generic response.
    """

    message = " ".join(redact_log_text(str(error)).split())
    if not message:
        return fallback
    return message[:MAX_PUBLIC_ERROR_CHARS]


def _safe_log_message(message: str) -> str:
    """Backward-compatible private alias for existing callers."""

    return redact_log_text(message)


@dataclass
class BoundedLogBuffer:
    maxlen: int = 1000
    _items: deque[dict[str, Any]] = field(init=False)
    _sequence: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        if self.maxlen < 1:
            raise ValueError("maxlen must be positive")
        self._items = deque(maxlen=self.maxlen)

    def append(
        self,
        *,
        level: str,
        source: str,
        message: str,
        pid: int | None = None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            entry = {
                "sequence": self._sequence,
                "timestamp": timestamp if timestamp is not None else time.time(),
                "level": level[:20],
                "source": source[:80],
                "pid": pid,
                "message": _safe_log_message(message),
            }
            self._items.append(entry)
            return dict(entry)

    def after(self, sequence: int) -> tuple[bool, list[dict[str, Any]]]:
        """Return available records after a cursor and whether earlier records were lost."""

        with self._lock:
            snapshot = [dict(item) for item in self._items]
        if not snapshot:
            return False, []
        first = int(snapshot[0]["sequence"])
        gap = sequence > 0 and first > sequence + 1
        return gap, [item for item in snapshot if int(item["sequence"]) > sequence]

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._items]

    @property
    def latest_sequence(self) -> int:
        with self._lock:
            return self._sequence
