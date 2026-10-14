"""Cross-process, secret-free credential selection for advisory model traffic.

Shared state stores hashes of environment reference names, a round-robin
cursor, and wall-clock cooldowns. It never stores the actual API keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence

import httpx
from filelock import FileLock, Timeout

_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,79}$")
_MAX_KEYS = 16
_MAX_STATE_BYTES = 16 * 1024
_MAX_COOLDOWN = 86_400.0


class CredentialsUnavailable(RuntimeError):
    """No eligible key or rotation state unavailable/corrupted."""


@dataclass(frozen=True)
class CredentialLease:
    reference: str
    secret: str = field(repr=False, compare=False)


def credential_references(*refs: str) -> tuple[str, ...]:
    """Validate environment variable names, never their secret values."""
    if len(refs) > _MAX_KEYS:
        raise ValueError("too many provider credential references")
    if len(refs) != len(set(refs)):
        raise ValueError("duplicate provider credential references")
    for name in refs:
        if not isinstance(name, str) or not _REF_RE.fullmatch(name):
            raise ValueError("invalid provider credential reference")
    return tuple(refs)


class CredentialPool:
    """File-locked fleet-wide credential rotation; fail closed on invalid state.

    Different keys do not increase provider-wide project or organization limits.
    """

    def __init__(
        self,
        references: Sequence[str],
        *,
        state_path: Path,
        env: Mapping[str, str] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.references = credential_references(*references)
        if not self.references:
            raise ValueError("credential pool requires a credential reference")
        self.state_path = Path(state_path)
        self.env = os.environ if env is None else env
        self._now = now
        self._ids = {
            name: hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]
            for name in self.references
        }

    def _state(self) -> dict:
        path = self.state_path
        if path.is_symlink():
            raise CredentialsUnavailable("credential rotation state is invalid")
        if not path.exists():
            return {"schema_version": 1, "cursor": 0, "cooldowns": {}}
        try:
            if path.stat().st_size > _MAX_STATE_BYTES:
                raise ValueError("oversized")
            state = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(state, dict)
                or set(state) != {"schema_version", "cursor", "cooldowns"}
                or type(state["schema_version"]) is not int
                or state["schema_version"] != 1
                or type(state["cursor"]) is not int
                or not 0 <= state["cursor"] <= 1_000_000_000
                or not isinstance(state["cooldowns"], dict)
                or len(state["cooldowns"]) > _MAX_KEYS
            ):
                raise ValueError("state shape")
            for slot, expiry in state["cooldowns"].items():
                if (
                    slot not in self._ids.values()
                    or type(expiry) not in (float, int)
                    or not 0 <= float(expiry) < 10_000_000_000
                ):
                    raise ValueError("cooldown shape")
            return state
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise CredentialsUnavailable("credential rotation state is invalid") from exc

    def _write(self, state: dict) -> None:
        path = self.state_path
        payload = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        if len(payload) > _MAX_STATE_BYTES:
            raise CredentialsUnavailable("credential rotation state is too large")
        name: str | None = None
        try:
            fd, name = tempfile.mkstemp(prefix=".credential-", dir=path.parent)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(name, 0o600)
            os.replace(name, path)
            name = None
        except OSError as exc:
            raise CredentialsUnavailable("credential rotation state is unwritable") from exc
        finally:
            if name is not None:
                try:
                    os.unlink(name)
                except OSError:
                    pass

    def _lock(self) -> FileLock:
        path = self.state_path
        if path.parent.is_symlink():
            raise CredentialsUnavailable("credential rotation directory is unsafe")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink() or Path(str(path) + ".lock").is_symlink():
            raise CredentialsUnavailable("credential rotation state is unsafe")
        return FileLock(str(path) + ".lock", timeout=2)

    def acquire(self, *, exclude: frozenset[str] = frozenset()) -> CredentialLease:
        try:
            with self._lock():
                state = self._state()
                ts = self._now()
                count = len(self.references)
                for step in range(count):
                    index = (state["cursor"] + step) % count
                    ref = self.references[index]
                    secret = self.env.get(ref, "")
                    if (
                        ref in exclude
                        or not isinstance(secret, str)
                        or not secret.strip()
                        or secret != secret.strip()
                        or "\n" in secret
                        or "\r" in secret
                        or state["cooldowns"].get(self._ids[ref], 0) > ts
                    ):
                        continue
                    state["cursor"] = (index + 1) % count
                    self._write(state)
                    return CredentialLease(ref, secret)
        except Timeout as exc:
            raise CredentialsUnavailable("credential rotation lock unavailable") from exc
        raise CredentialsUnavailable("no eligible provider credential")

    def cool_down_all(self, seconds: float) -> None:
        """Honor provider-level Retry-After shared by every project key."""
        if (
            not isinstance(seconds, (int, float))
            or isinstance(seconds, bool)
            or not 0 < seconds <= _MAX_COOLDOWN
        ):
            raise ValueError("invalid cooldown")
        try:
            with self._lock():
                state = self._state()
                until = self._now() + seconds
                for slot in self._ids.values():
                    state["cooldowns"][slot] = max(
                        until, state["cooldowns"].get(slot, 0)
                    )
                self._write(state)
        except Timeout as exc:
            raise CredentialsUnavailable("credential rotation lock unavailable") from exc

    def cool_down(self, reference: str, seconds: float) -> None:
        if reference not in self._ids:
            raise ValueError("unknown credential reference")
        if (
            not isinstance(seconds, (int, float))
            or isinstance(seconds, bool)
            or not 0 < seconds <= _MAX_COOLDOWN
        ):
            raise ValueError("invalid cooldown")
        try:
            with self._lock():
                state = self._state()
                slot = self._ids[reference]
                state["cooldowns"][slot] = max(
                    self._now() + seconds, state["cooldowns"].get(slot, 0)
                )
                self._write(state)
        except Timeout as exc:
            raise CredentialsUnavailable("credential rotation lock unavailable") from exc


def default_rotation_path(
    provider_id: str, base_url: str, model: str, references: Sequence[str] = ()
) -> Path:
    """Scope shared state to the provider/model/credential reference set."""
    from trading_gym.common.persistence import resolve_data_home

    discriminator = hashlib.sha256(
        "\0".join((provider_id, base_url, model, *references)).encode("utf-8")
    ).hexdigest()[:24]
    return resolve_data_home() / "intelligence" / f"keys-{discriminator}.json"


def _retry_after_seconds(value: str | None, *, now: float) -> float:
    """Parse Retry-After without sleeping inside the trading loop."""
    if not value:
        return 5.0
    try:
        seconds = float(value)
        if not 0 < seconds <= _MAX_COOLDOWN:
            return _MAX_COOLDOWN
        return seconds
    except ValueError:
        pass
    try:
        requested = parsedate_to_datetime(value)
        if requested.tzinfo is None:
            requested = requested.replace(tzinfo=timezone.utc)
        duration = requested.timestamp() - now
        return min(max(duration, 1.0), _MAX_COOLDOWN)
    except (TypeError, ValueError, OverflowError):
        return 5.0


def _quota_exhausted(response: object) -> bool:
    """Do not rotate keys to evade a project-wide hard quota/spend cap."""
    try:
        payload = response.json()
        body = payload.get("error", {}) if isinstance(payload, dict) else {}
        code = str(body.get("code", "")) if isinstance(body, dict) else ""
        return code in {
            "insufficient_quota",
            "billing_hard_limit_reached",
            "quota_exceeded",
        }
    except (ValueError, AttributeError, TypeError):
        return False


def request_with_rotation(
    pool: CredentialPool,
    post: Callable[[str, float], object],
    *,
    timeout_seconds: float,
    max_attempts: int = 3,
    rotate_on_rate_limit: bool = False,
    monotonic: Callable[[], float] = time.monotonic,
) -> object:
    """Bounded nonblocking failover; never expose keys or bypass 403 policy.

    The timeout is for the entire advisory request, not for each retry.
    No key retries occur after a quota/spend cap or explicit scope denial.
    """
    if not 0 < timeout_seconds <= 120:
        raise ValueError("invalid LLM request deadline")
    if not 1 <= max_attempts <= _MAX_KEYS:
        raise ValueError("invalid LLM attempt budget")
    deadline = monotonic() + timeout_seconds
    tried: set[str] = set()
    tried_values: set[bytes] = set()
    for _ in range(min(max_attempts, len(pool.references))):
        if monotonic() >= deadline:
            break
        try:
            lease = pool.acquire(exclude=frozenset(tried))
        except CredentialsUnavailable:
            break
        tried.add(lease.reference)
        fingerprint = hashlib.sha256(lease.secret.encode()).digest()
        if fingerprint in tried_values:
            continue
        tried_values.add(fingerprint)
        try:
            response = post(lease.secret, max(0.001, deadline - monotonic()))
        except (httpx.TimeoutException, httpx.NetworkError):
            pool.cool_down(lease.reference, 5)
            continue
        code = getattr(response, "status_code", None)
        if type(code) is not int:
            raise CredentialsUnavailable("invalid provider HTTP response")
        if 200 <= code < 300:
            return response
        if code == 403:
            raise PermissionError("provider_access_denied")
        if code in (400, 402, 404, 422):
            raise CredentialsUnavailable(f"provider_request_rejected_{code}")
        if code == 429:
            headers = getattr(response, "headers", {})
            retry_after = _retry_after_seconds(
                headers.get("retry-after"), now=pool._now()
            )
            if _quota_exhausted(response) or not rotate_on_rate_limit:
                pool.cool_down_all(retry_after)
                raise CredentialsUnavailable("provider_rate_or_quota_limit")
            pool.cool_down(lease.reference, retry_after)
            continue
        if code == 401:
            pool.cool_down(lease.reference, 3_600)
            continue
        if code in (408, 425) or 500 <= code <= 599:
            pool.cool_down(lease.reference, 5)
            continue
        raise CredentialsUnavailable(f"provider_request_rejected_{code}")
    raise CredentialsUnavailable("no healthy provider credential within deadline")
