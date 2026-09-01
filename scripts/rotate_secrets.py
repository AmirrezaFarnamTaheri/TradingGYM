#!/usr/bin/env python3
"""Safely inspect or rotate one local control-plane key in an env file.

Checking is the default. Mutation requires ``--apply``. The command never
prints generated secret material and does not handle actor/audit keyrings,
provider credentials, or broker credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from trading_gym.common.persistence import atomic_write_text


MAX_ENV_BYTES = 1024 * 1024
SUPPORTED_KEYS = {
    "API_KEY",
    "WORKER_API_KEY",
    "APPROVAL_API_KEY",
    "EMERGENCY_API_KEY",
}
INSECURE_VALUES = {
    "",
    "changeme",
    "changeme-insecure-default",
    "replace-with-random-api-key",
    "replace-with-independent-random-worker-key",
    "replace-with-separate-random-approval-key",
    "replace-with-separate-random-emergency-key",
}
LINE_RE = re.compile(r"^(?P<prefix>[ \t]*)(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>.*)$")


class SecretRotationError(RuntimeError):
    """The env file or requested rotation is unsafe or ambiguous."""


@dataclass(frozen=True)
class RotationResult:
    key_name: str
    present: bool
    secure: bool
    changed: bool
    fingerprint_sha256: str | None


def _read_env(path: Path) -> tuple[str, os.stat_result]:
    if path.is_symlink():
        raise SecretRotationError("env file must not be a symbolic link")
    if not path.is_file():
        raise SecretRotationError("env file does not exist or is not a regular file")
    stat = path.stat()
    if stat.st_size > MAX_ENV_BYTES:
        raise SecretRotationError("env file exceeds the bounded size limit")
    return path.read_text(encoding="utf-8"), stat


def _records(content: str) -> dict[str, list[tuple[int, str]]]:
    records: dict[str, list[tuple[int, str]]] = {}
    for index, line in enumerate(content.splitlines()):
        match = LINE_RE.match(line)
        if match:
            records.setdefault(match.group("key"), []).append((index, match.group("value")))
    return records


def _secure(value: str) -> bool:
    stripped = value.strip().strip('"').strip("'")
    return stripped not in INSECURE_VALUES and len(stripped.encode("utf-8")) >= 32


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def rotate_env_key(
    path: Path,
    *,
    key_name: str,
    apply: bool = False,
    create: bool = False,
    token_bytes: int = 48,
) -> RotationResult:
    if key_name not in SUPPORTED_KEYS:
        raise SecretRotationError("unsupported key name")
    if token_bytes < 32 or token_bytes > 256:
        raise SecretRotationError("token_bytes must be between 32 and 256")
    content, stat = _read_env(path)
    records = _records(content)
    matches = records.get(key_name, [])
    if len(matches) > 1:
        raise SecretRotationError(f"duplicate {key_name} definitions")
    if not matches and not create:
        return RotationResult(key_name, False, False, False, None)
    current = matches[0][1] if matches else ""
    current_clean = current.strip().strip('"').strip("'")
    if not apply:
        return RotationResult(
            key_name=key_name,
            present=bool(matches),
            secure=_secure(current),
            changed=False,
            fingerprint_sha256=_fingerprint(current_clean) if current_clean else None,
        )

    generated = secrets.token_urlsafe(token_bytes)
    other_values = {
        values[0][1].strip().strip('"').strip("'")
        for name, values in records.items()
        if name in SUPPORTED_KEYS and name != key_name and len(values) == 1
    }
    if generated in other_values:  # cryptographically implausible, but authority reuse still fails closed
        raise SecretRotationError("generated key collides with another control credential")

    lines = content.splitlines()
    if matches:
        index = matches[0][0]
        prefix = LINE_RE.match(lines[index]).group("prefix")  # type: ignore[union-attr]
        lines[index] = f"{prefix}{key_name}={generated}"
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key_name}={generated}")
    trailing_newline = content.endswith("\n") or not content
    updated = "\n".join(lines) + ("\n" if trailing_newline else "")
    atomic_write_text(path, updated, max_bytes=MAX_ENV_BYTES, mode=0o600)
    path.chmod(0o600)
    if path.stat().st_size > MAX_ENV_BYTES:
        raise SecretRotationError("rotated env file exceeds the bounded size limit")
    return RotationResult(
        key_name=key_name,
        present=True,
        secure=True,
        changed=True,
        fingerprint_sha256=_fingerprint(generated),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--key-name", choices=sorted(SUPPORTED_KEYS), default="API_KEY")
    parser.add_argument("--apply", action="store_true", help="perform an atomic rotation")
    parser.add_argument("--create", action="store_true", help="create a missing key (requires --apply)")
    parser.add_argument("--token-bytes", type=int, default=48)
    args = parser.parse_args()
    if args.create and not args.apply:
        parser.error("--create requires --apply")
    try:
        result = rotate_env_key(
            args.env_file,
            key_name=args.key_name,
            apply=args.apply,
            create=args.create,
            token_bytes=args.token_bytes,
        )
    except (OSError, UnicodeError, SecretRotationError) as exc:
        print(f"rotation failed: {type(exc).__name__}")
        return 1
    if not result.present:
        print(f"{result.key_name}: missing; no change performed")
        return 2
    if result.changed:
        print(f"{result.key_name}: rotated atomically; restart dependent services")
    else:
        print(f"{result.key_name}: {'secure' if result.secure else 'rotation required'}; no change performed")
    return 0 if result.secure else 3


if __name__ == "__main__":
    raise SystemExit(main())
