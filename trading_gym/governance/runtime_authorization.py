"""Single-use startup barrier for governed live-trading workers.

The API reserves an approved proposal, mints a private startup artifact, spawns the
worker, commits the reservation, and finally publishes a matching commit artifact.
The worker cannot construct a live execution adapter until it has atomically
consumed both artifacts with the secret token supplied only in its environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from trading_gym.common.persistence import atomic_write_json, resolve_data_home
from trading_gym.common.security import confined_child

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_AUTH_BYTES = 16 * 1024


@dataclass(frozen=True)
class RuntimeAuthorization:
    authorization_id: str
    token: str
    start_path: Path
    commit_path: Path
    proposal_id: str
    reservation_id: str
    model_sha256: str
    actor: str
    live_scope: str
    consent_expires_at: float
    expires_at: float


def _root() -> Path:
    root = resolve_data_home() / "runtime-authorizations"
    if root.exists() and root.is_symlink():
        raise ValueError("runtime authorization root must not be a symbolic link")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def _validate_identity(value: str, *, label: str, pattern: re.Pattern[str]) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"{label} has an invalid format")
    return value


def issue_runtime_authorization(
    *,
    proposal_id: str,
    reservation_id: str,
    model_sha256: str,
    actor: str = "system",
    ttl_seconds: int = 45,
    consent_ttl_seconds: int = 8 * 60 * 60,
) -> RuntimeAuthorization:
    """Mint one private startup artifact for a reserved live operation."""

    _validate_identity(proposal_id, label="proposal id", pattern=_HEX32)
    _validate_identity(reservation_id, label="reservation id", pattern=_HEX32)
    _validate_identity(model_sha256, label="model sha256", pattern=_HEX64)
    if not 10 <= ttl_seconds <= 120:
        raise ValueError("runtime authorization ttl must be in [10, 120] seconds")
    actor = actor.strip()
    if not actor or len(actor) > 120:
        raise ValueError("runtime authorization actor must be 1-120 characters")
    if not 60 <= consent_ttl_seconds <= 24 * 60 * 60:
        raise ValueError("live consent ttl must be in [60, 86400] seconds")

    authorization_id = secrets.token_hex(16)
    token = secrets.token_urlsafe(32)
    root = _root()
    start_path = confined_child(root, f"{authorization_id}.start.json")
    commit_path = confined_child(root, f"{authorization_id}.commit.json")
    now = time.time()
    expires_at = now + ttl_seconds
    consent_expires_at = now + consent_ttl_seconds
    live_scope = "live.execute"
    atomic_write_json(
        start_path,
        {
            "schema_version": 1,
            "purpose": "trading.start_live",
            "authorization_id": authorization_id,
            "proposal_id": proposal_id,
            "reservation_id": reservation_id,
            "model_sha256": model_sha256,
            "actor": actor,
            "live_scope": live_scope,
            "consent_expires_at": consent_expires_at,
            "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "issued_at": now,
            "expires_at": expires_at,
        },
    )
    return RuntimeAuthorization(
        authorization_id=authorization_id,
        token=token,
        start_path=start_path,
        commit_path=commit_path,
        proposal_id=proposal_id,
        reservation_id=reservation_id,
        model_sha256=model_sha256,
        actor=actor,
        live_scope=live_scope,
        consent_expires_at=consent_expires_at,
        expires_at=expires_at,
    )


def commit_runtime_authorization(authorization: RuntimeAuthorization, *, pid: int) -> None:
    """Publish the post-governance-commit barrier for the exact spawned PID."""

    if pid <= 0:
        raise ValueError("runtime authorization pid must be positive")
    if time.time() >= authorization.expires_at:
        raise ValueError("runtime authorization expired before commit")
    atomic_write_json(
        authorization.commit_path,
        {
            "schema_version": 1,
            "purpose": "trading.start_live.committed",
            "authorization_id": authorization.authorization_id,
            "proposal_id": authorization.proposal_id,
            "reservation_id": authorization.reservation_id,
            "model_sha256": authorization.model_sha256,
            "actor": authorization.actor,
            "live_scope": authorization.live_scope,
            "consent_expires_at": authorization.consent_expires_at,
            "token_sha256": hashlib.sha256(authorization.token.encode("utf-8")).hexdigest(),
            "pid": pid,
            "committed_at": time.time(),
            "expires_at": authorization.expires_at,
        },
    )


def _claim(path: Path, *, suffix: str) -> tuple[Path, dict[str, Any]]:
    root = _root()
    supplied = Path(path).expanduser()
    if supplied.name != path.name:
        raise ValueError("runtime authorization path is invalid")
    expected = confined_child(root, supplied.name, must_exist=True)
    if expected != supplied.resolve(strict=False):
        raise ValueError("runtime authorization path is outside its configured root")
    if expected.is_symlink() or not expected.is_file():
        raise ValueError("runtime authorization must be a regular file")
    claim = confined_child(root, f".{expected.stem}.{os.getpid()}.{suffix}")
    with FileLock(str(expected) + ".lock", timeout=5):
        if expected.is_symlink() or not expected.is_file():
            raise ValueError("runtime authorization was already consumed")
        os.replace(expected, claim)
    try:
        if claim.stat().st_size > MAX_AUTH_BYTES:
            raise ValueError("runtime authorization exceeds its size limit")
        raw = json.loads(claim.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("runtime authorization must contain a JSON object")
        return claim, raw
    except Exception:
        claim.unlink(missing_ok=True)
        raise


def _validate_payload(
    payload: dict[str, Any],
    *,
    purpose: str,
    token: str,
    expected_model_sha256: str,
    authorization_id: str | None = None,
    expected_pid: int | None = None,
) -> None:
    required = {
        "schema_version",
        "purpose",
        "authorization_id",
        "proposal_id",
        "reservation_id",
        "model_sha256",
        "actor",
        "live_scope",
        "consent_expires_at",
        "token_sha256",
        "expires_at",
    }
    if not required.issubset(payload):
        raise ValueError("runtime authorization is incomplete")
    if payload["schema_version"] != 1 or payload["purpose"] != purpose:
        raise ValueError("runtime authorization schema or purpose is invalid")
    if authorization_id is not None and payload["authorization_id"] != authorization_id:
        raise ValueError("runtime authorization identity does not match")
    _validate_identity(str(payload["authorization_id"]), label="authorization id", pattern=_HEX32)
    _validate_identity(str(payload["proposal_id"]), label="proposal id", pattern=_HEX32)
    _validate_identity(str(payload["reservation_id"]), label="reservation id", pattern=_HEX32)
    _validate_identity(str(payload["model_sha256"]), label="model sha256", pattern=_HEX64)
    if not secrets.compare_digest(str(payload["model_sha256"]), expected_model_sha256):
        raise ValueError("runtime authorization model identity does not match")
    actor = str(payload["actor"]).strip()
    if not actor or len(actor) > 120:
        raise ValueError("runtime authorization actor is invalid")
    if payload["live_scope"] != "live.execute":
        raise ValueError("runtime authorization live scope is invalid")
    consent_expires_at = float(payload["consent_expires_at"])
    if not time.time() < consent_expires_at <= time.time() + 24 * 60 * 60:
        raise ValueError("runtime authorization live consent is expired or invalid")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(str(payload["token_sha256"]), token_hash):
        raise ValueError("runtime authorization token does not match")
    expires_at = float(payload["expires_at"])
    if not time.time() < expires_at <= time.time() + 120:
        raise ValueError("runtime authorization is expired or has an invalid lifetime")
    if expected_pid is not None and int(payload.get("pid", -1)) != expected_pid:
        raise ValueError("runtime authorization pid does not match")


def consume_runtime_authorization(
    start_path: Path,
    *,
    token: str,
    expected_model_sha256: str,
    commit_timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Consume start+commit barriers exactly once and return safe identifiers."""

    if not token or len(token.encode("utf-8")) > 256:
        raise ValueError("runtime authorization token is missing or oversized")
    if not 1 <= commit_timeout_seconds <= 60:
        raise ValueError("runtime authorization wait must be in [1, 60] seconds")
    _validate_identity(expected_model_sha256, label="model sha256", pattern=_HEX64)

    start_claim, start = _claim(start_path, suffix="start.claimed")
    try:
        _validate_payload(
            start,
            purpose="trading.start_live",
            token=token,
            expected_model_sha256=expected_model_sha256,
        )
        authorization_id = str(start["authorization_id"])
        commit_path = confined_child(_root(), f"{authorization_id}.commit.json")
        deadline = time.monotonic() + commit_timeout_seconds
        while not commit_path.exists():
            if time.monotonic() >= deadline or time.time() >= float(start["expires_at"]):
                raise TimeoutError("runtime authorization commit was not published")
            time.sleep(0.05)
        commit_claim, commit = _claim(commit_path, suffix="commit.claimed")
        try:
            _validate_payload(
                commit,
                purpose="trading.start_live.committed",
                token=token,
                expected_model_sha256=expected_model_sha256,
                authorization_id=authorization_id,
                expected_pid=os.getpid(),
            )
            if commit["proposal_id"] != start["proposal_id"] or commit["reservation_id"] != start["reservation_id"]:
                raise ValueError("runtime authorization commit does not match its reservation")
            return {
                "authorization_id": authorization_id,
                "proposal_id": str(start["proposal_id"]),
                "reservation_id": str(start["reservation_id"]),
                "live_consent": {
                    "actor": str(start["actor"]),
                    "scope": str(start["live_scope"]),
                    "expires_at": datetime.fromtimestamp(
                        float(start["consent_expires_at"]),
                        tz=timezone.utc,
                    ).isoformat(),
                    "nonce": authorization_id,
                },
            }
        finally:
            commit_claim.unlink(missing_ok=True)
    finally:
        start_claim.unlink(missing_ok=True)


def cleanup_runtime_authorization(authorization: RuntimeAuthorization | None) -> None:
    if authorization is None:
        return
    for path in (authorization.start_path, authorization.commit_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
