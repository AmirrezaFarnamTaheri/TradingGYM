"""Shared security primitives for agent, research, and governance surfaces.

The helpers in this module deliberately avoid storing or returning credentials,
bound recursive traversal, and fail closed around network destinations that are
commonly abused for server-side request forgery.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import stat
import tempfile
from contextlib import contextmanager
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

MAX_JSON_BYTES = 512 * 1024
MAX_REDACTION_DEPTH = 8

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "api_token",
        "apikey",
        "secret_key",
        "secretkey",
        "passphrase",
        "secret",
        "password",
        "private_key",
        "privatekey",
        "token",
        "access_token",
        "accesstoken",
        "refresh_token",
        "refreshtoken",
        "bot_token",
        "bottoken",
        "webhook_secret",
        "webhooksecret",
        "signing_secret",
        "signingsecret",
        "client_secret",
        "clientsecret",
        "authorization",
        "cookie",
        "set_cookie",
    }
)

_SECRET_KEY_SUFFIXES = (
    "_api_key",
    "_api_token",
    "_access_token",
    "_refresh_token",
    "_bot_token",
    "_private_key",
    "_secret_key",
    "_signing_secret",
    "_webhook_secret",
    "_client_secret",
    "_password",
    "_passphrase",
)

_METADATA_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
)
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _normalise_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _is_secret_key(value: str) -> bool:
    normalised = _normalise_key(value)
    return normalised in _SECRET_KEYS or normalised.endswith(_SECRET_KEY_SUFFIXES)


def redact_secrets(value: Any, *, depth: int = 0, max_depth: int = MAX_REDACTION_DEPTH) -> Any:
    """Return a bounded copy with credential-like fields masked.

    Values beyond ``max_depth`` are replaced rather than traversed. This avoids
    unbounded or adversarially nested payloads leaking through a diagnostic path.
    """

    if depth > max_depth:
        return "***"
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if _is_secret_key(text_key) and item not in (None, "", False):
                output[text_key] = "***"
            else:
                output[text_key] = redact_secrets(item, depth=depth + 1, max_depth=max_depth)
        return output
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item, depth=depth + 1, max_depth=max_depth) for item in value]
    return value


def bounded_json_dumps(value: Any, *, max_bytes: int = MAX_JSON_BYTES) -> str:
    """Serialise redacted JSON and reject payloads above ``max_bytes``."""

    encoded = json.dumps(redact_secrets(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    size = len(encoded.encode("utf-8"))
    if size > max_bytes:
        raise ValueError(f"JSON payload exceeds {max_bytes} byte limit")
    return encoded


def assert_payload_size(value: str | bytes, *, max_bytes: int = MAX_JSON_BYTES, label: str = "Payload") -> None:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    if len(raw) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} byte limit")


def _literal_ip_blocked(host: str, *, allow_private: bool, allow_loopback: bool) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    if any(address in network for network in _METADATA_NETWORKS) or address.is_link_local:
        return True
    if address.is_loopback:
        return not allow_loopback
    if any(address in network for network in _PRIVATE_NETWORKS) or address.is_private:
        return not allow_private
    return not address.is_global


def validate_base_url(
    url: str,
    *,
    allow_private: bool = False,
    allow_loopback: bool = False,
    resolve_dns: bool = True,
) -> str:
    """Validate an HTTP(S) endpoint and return a normalised URL.

    Cloud metadata/link-local destinations are always rejected. Private and
    loopback destinations require explicit opt-in, allowing local model runtimes
    without making a remotely exposed deployment an SSRF proxy.
    """

    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Base URL must use http:// or https://")
    if parsed.username or parsed.password:
        raise ValueError("Credentials must not be embedded in a Base URL")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("Base URL is missing a hostname")
    if _literal_ip_blocked(host, allow_private=allow_private, allow_loopback=allow_loopback):
        raise ValueError("Base URL points to a disallowed network destination")

    if resolve_dns and not _is_literal_ip(host):
        try:
            resolved = {info[4][0] for info in socket.getaddrinfo(host, parsed.port or None)}
        except socket.gaierror as exc:
            raise ValueError("Base URL hostname could not be resolved") from exc
        for address in resolved:
            if _literal_ip_blocked(
                address,
                allow_private=allow_private,
                allow_loopback=allow_loopback,
            ):
                raise ValueError("Base URL resolves to a disallowed network destination")

    path = parsed.path.rstrip("/")
    normalised = parsed._replace(path=path, params="", query="", fragment="").geturl()
    return normalised.rstrip("/")


def _is_literal_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def confined_child(
    root: Path,
    *parts: str,
    must_exist: bool = False,
    allow_symlinks: bool = False,
) -> Path:
    """Resolve a child path without permitting traversal or symlink escape.

    ``root`` is an administrator-selected trust boundary.  User-controlled path
    components must remain below it after resolution.  Existing symlink
    components are rejected by default even when they ultimately resolve inside
    the root, avoiding time-of-check/time-of-use surprises for model and config
    files.
    """

    if not parts or any(not isinstance(part, str) or not part for part in parts):
        raise ValueError("path components must be non-empty strings")
    if any("\x00" in part or any(ord(char) < 32 for char in part) for part in parts):
        raise ValueError("path components contain control characters")

    resolved_root = root.expanduser().resolve(strict=False)
    candidate = resolved_root.joinpath(*parts)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("path escapes its configured root") from exc

    if not allow_symlinks:
        current = resolved_root
        for part in candidate.relative_to(resolved_root).parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise ValueError("symlink-backed paths are not allowed")

    if must_exist and not resolved_candidate.exists():
        raise FileNotFoundError(resolved_candidate)
    return resolved_candidate


def validate_regular_file(
    value: str | Path,
    *,
    label: str = "file",
    max_bytes: int,
) -> Path:
    """Resolve one bounded regular file while rejecting symlink-backed inputs."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    path = Path(value).expanduser()
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if metadata.st_size > max_bytes:
        raise ValueError(f"{label} exceeds its size limit")
    return path.resolve()


@contextmanager
def verified_regular_file_snapshot(
    value: str | Path,
    *,
    expected_sha256: str,
    label: str = "file",
    max_bytes: int,
) -> Iterator[Path]:
    """Yield a private immutable snapshot after streaming SHA-256 verification.

    The source is opened first and copied through that descriptor, so replacing
    the path after validation cannot swap the bytes consumed by a deserialiser.
    A changed in-place source is also rejected by comparing descriptor metadata
    before and after the copy.
    """

    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or ""):
        raise ValueError(f"{label} expected SHA-256 is invalid")
    path = validate_regular_file(value, label=label, max_bytes=max_bytes)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    temp_path: Path | None = None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if before.st_size > max_bytes:
            raise ValueError(f"{label} exceeds its size limit")
        temp_descriptor, temp_name = tempfile.mkstemp(prefix=".trading-gym-verified-", suffix=path.suffix)
        temp_path = Path(temp_name)
        digest = hashlib.sha256()
        copied = 0
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as source, os.fdopen(
                temp_descriptor, "wb"
            ) as target:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > max_bytes:
                        raise ValueError(f"{label} exceeds its size limit")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            after = os.fstat(descriptor)
            identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            if identity_before != identity_after or copied != before.st_size:
                raise ValueError(f"{label} changed while it was being verified")
            if not secrets.compare_digest(digest.hexdigest(), expected_sha256):
                raise ValueError(f"{label} SHA-256 mismatch")
            try:
                temp_path.chmod(0o600)
            except OSError:
                pass
            yield temp_path
        finally:
            if temp_path.exists():
                temp_path.unlink()
    finally:
        os.close(descriptor)


def redact_secret_text(value: str) -> str:
    """Redact secret-like text through the canonical structured redactor."""
    result = redact_secrets(value)
    return str(result)
