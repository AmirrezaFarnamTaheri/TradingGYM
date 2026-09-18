"""Append-only audit ledger with hash chaining and rotatable HMAC sealing.

A hash chain detects accidental corruption and unsophisticated edits.  HMAC
sealing authenticates each new entry and an external head anchor.  A bounded
keyring permits deliberate key rotation without making historical entries
unverifiable.  Secrets never appear in ledger records or verification reports.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from filelock import FileLock

from trading_gym.common.persistence import (
    MAX_JSONL_RECORD_BYTES,
    _fsync_directory,
    _reject_symlink,
    append_jsonl,
    atomic_write_json,
    read_json,
    resolve_data_home,
)
from trading_gym.common.security import bounded_json_dumps, redact_secrets

GENESIS_HASH = "0" * 64
MAX_LEDGER_BYTES = 512 * 1024 * 1024
MAX_AUDIT_ENTRIES = 1_000_000
MAX_AUDIT_KEYS = 16
KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _coerce_secret(value: str | bytes, *, label: str) -> bytes:
    encoded = value if isinstance(value, bytes) else value.encode("utf-8")
    if len(encoded) < 32:
        raise ValueError(f"{label} must contain at least 32 bytes")
    return encoded


def _validate_key_id(value: str) -> str:
    value = value.strip()
    if not KEY_ID_RE.fullmatch(value):
        raise ValueError(
            "audit HMAC key IDs must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}"
        )
    return value


def _parse_environment_keyring() -> Mapping[str, str | bytes] | None:
    raw = os.getenv("AUDIT_HMAC_KEYRING_JSON")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("AUDIT_HMAC_KEYRING_JSON must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("AUDIT_HMAC_KEYRING_JSON must be a JSON object")
    return parsed


def _load_key_configuration(
    *,
    hmac_key: str | bytes | None,
    hmac_key_id: str | None,
    hmac_keyring: Mapping[str, str | bytes] | None,
) -> tuple[dict[str, bytes], str | None]:
    """Resolve explicit or environment key configuration without exposing it."""

    explicit_single = hmac_key is not None
    explicit_ring = hmac_keyring is not None
    if explicit_single and explicit_ring:
        raise ValueError("configure either hmac_key or hmac_keyring, not both")

    if explicit_ring:
        ring_source = hmac_keyring
        single_source: str | bytes | None = None
    elif explicit_single:
        ring_source = None
        single_source = hmac_key
    else:
        ring_source = _parse_environment_keyring()
        if ring_source is not None and os.getenv("AUDIT_HMAC_KEY"):
            raise ValueError(
                "configure AUDIT_HMAC_KEY or AUDIT_HMAC_KEYRING_JSON, not both"
            )
        single_source = None if ring_source is not None else os.getenv("AUDIT_HMAC_KEY")

    if ring_source is not None:
        if not ring_source:
            raise ValueError("audit HMAC keyring must not be empty")
        if len(ring_source) > MAX_AUDIT_KEYS:
            raise ValueError(f"audit HMAC keyring exceeds {MAX_AUDIT_KEYS} keys")
        keys: dict[str, bytes] = {}
        fingerprints: set[str] = set()
        for raw_id, raw_secret in ring_source.items():
            if not isinstance(raw_id, str) or not isinstance(raw_secret, (str, bytes)):
                raise ValueError(
                    "audit HMAC keyring entries must map string IDs to string secrets"
                )
            key_id = _validate_key_id(raw_id)
            secret = _coerce_secret(raw_secret, label=f"audit HMAC key {key_id}")
            fingerprint = hashlib.sha256(secret).hexdigest()
            if fingerprint in fingerprints:
                raise ValueError(
                    "audit HMAC keyring must not reuse one secret under multiple IDs"
                )
            fingerprints.add(fingerprint)
            keys[key_id] = secret
        active = hmac_key_id or os.getenv("AUDIT_HMAC_KEY_ID")
        if not active:
            raise ValueError(
                "AUDIT_HMAC_KEY_ID is required when an audit keyring is configured"
            )
        active = _validate_key_id(active)
        if active not in keys:
            raise ValueError("active audit HMAC key ID is not present in the keyring")
        return keys, active

    if single_source is None or single_source == "":
        return {}, None
    key_id = _validate_key_id(hmac_key_id or os.getenv("AUDIT_HMAC_KEY_ID") or "legacy")
    return {key_id: _coerce_secret(single_source, label="AUDIT_HMAC_KEY")}, key_id


class AuditLedger:
    def __init__(
        self,
        path: Path | None = None,
        *,
        hmac_key: str | bytes | None = None,
        hmac_key_id: str | None = None,
        hmac_keyring: Mapping[str, str | bytes] | None = None,
        anchor_path: Path | None = None,
    ) -> None:
        self.path = path or resolve_data_home() / "audit-ledger.jsonl"
        self.anchor_path = anchor_path or self.path.with_suffix(
            self.path.suffix + ".anchor.json"
        )
        self._keys, self._active_key_id = _load_key_configuration(
            hmac_key=hmac_key,
            hmac_key_id=hmac_key_id,
            hmac_keyring=hmac_keyring,
        )
        # Retain the historical private attribute for callers that only inspect
        # whether sealing is configured; all MAC operations use the keyring.
        self._key = self._keys.get(self._active_key_id) if self._active_key_id else None
        self.lock = FileLock(str(self.path) + ".chain.lock")
        # Verified-state cache: verify() re-reads and re-validates the whole
        # chain, which is O(N) per call and O(N^2) across N appends when a
        # caller verifies after every write. Cache the last verified result
        # keyed by file identity so an unchanged ledger is verified once.
        self._verified_signature: tuple[float, int] | None = None
        self._verified_cache: dict[str, Any] | None = None

    @property
    def sealing_enabled(self) -> bool:
        return self._active_key_id is not None

    @property
    def active_key_id(self) -> str | None:
        return self._active_key_id

    def _entries_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        if self.path.is_symlink():
            raise ValueError("audit ledger must not be a symbolic link")
        if self.path.stat().st_size > MAX_LEDGER_BYTES:
            raise ValueError("audit ledger exceeds its configured size limit")
        entries: list[dict[str, Any]] = []
        for number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"audit ledger has invalid JSON at line {number}"
                ) from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"audit ledger line {number} is not an object")
            entries.append(parsed)
            if len(entries) > MAX_AUDIT_ENTRIES:
                raise ValueError("audit ledger exceeds its configured entry limit")
        return entries

    def _mac(
        self, purpose: str, value: dict[str, Any], *, key_id: str | None = None
    ) -> str:
        selected = key_id or self._active_key_id
        if selected is None or selected not in self._keys:
            raise RuntimeError("audit sealing key is not configured")
        return hmac.new(
            self._keys[selected],
            purpose.encode("ascii") + b"\0" + _canonical(value),
            hashlib.sha256,
        ).hexdigest()

    def _matching_key_id(
        self,
        purpose: str,
        value: dict[str, Any],
        actual_mac: Any,
        *,
        declared_key_id: Any = None,
    ) -> str | None:
        if not isinstance(actual_mac, str):
            return None
        if declared_key_id is not None:
            if (
                not isinstance(declared_key_id, str)
                or declared_key_id not in self._keys
            ):
                return None
            candidates = (declared_key_id,)
        else:
            # Legacy sealed records carried no key ID.  Trying the bounded
            # configured keyring preserves rotation continuity without guessing.
            candidates = tuple(sorted(self._keys))
        for key_id in candidates:
            expected = self._mac(purpose, value, key_id=key_id)
            if hmac.compare_digest(actual_mac, expected):
                return key_id
        return None

    def _write_anchor(self, *, sequence: int, head: str) -> None:
        if self._active_key_id is None:
            raise RuntimeError("audit sealing is not configured")
        anchor_body = {
            "schema_version": 2,
            "key_id": self._active_key_id,
            "sequence": sequence,
            "head": head,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        anchor_body["anchor_mac"] = self._mac("audit-anchor-v1", anchor_body)
        atomic_write_json(self.anchor_path, anchor_body)

    def _verify_entries(
        self,
        entries: list[dict[str, Any]],
    ) -> tuple[str, list[int], set[str], str | None, bool]:
        previous_hash = GENESIS_HASH
        sealed_sequences: list[int] = []
        key_ids_used: set[str] = set()
        for expected_sequence, entry in enumerate(entries, start=1):
            if entry.get("sequence") != expected_sequence:
                return (
                    previous_hash,
                    sealed_sequences,
                    key_ids_used,
                    f"sequence mismatch at {expected_sequence}",
                    False,
                )
            if entry.get("previous_hash") != previous_hash:
                return (
                    previous_hash,
                    sealed_sequences,
                    key_ids_used,
                    f"chain mismatch at {expected_sequence}",
                    False,
                )
            candidate = dict(entry)
            entry_mac = candidate.pop("entry_mac", None)
            entry_hash = candidate.pop("entry_hash", None)
            computed = hashlib.sha256(
                previous_hash.encode("ascii") + _canonical(candidate)
            ).hexdigest()
            if not isinstance(entry_hash, str) or not hmac.compare_digest(
                entry_hash, computed
            ):
                return (
                    previous_hash,
                    sealed_sequences,
                    key_ids_used,
                    f"hash mismatch at {expected_sequence}",
                    False,
                )
            if entry_mac is not None:
                mac_candidate = dict(entry)
                mac_candidate.pop("entry_mac", None)
                matched = self._matching_key_id(
                    "audit-entry-v1",
                    mac_candidate,
                    entry_mac,
                    declared_key_id=entry.get("entry_key_id"),
                )
                if matched is None:
                    if not self._keys:
                        error = "sealed entries are present but audit HMAC keys are unavailable"
                    elif entry.get("entry_key_id") not in (None, *self._keys.keys()):
                        error = f"unknown entry key ID at {expected_sequence}"
                    else:
                        error = f"entry MAC mismatch at {expected_sequence}"
                    return entry_hash, sealed_sequences, key_ids_used, error, True
                sealed_sequences.append(expected_sequence)
                key_ids_used.add(matched)
            previous_hash = entry_hash
        return previous_hash, sealed_sequences, key_ids_used, None, True

    def append(
        self,
        *,
        event: str,
        actor: str,
        subject: str,
        details: dict[str, Any],
        event_id: str | None = None,
    ) -> dict[str, Any]:
        event = event.strip()
        actor = actor.strip()
        subject = subject.strip()
        if not event or not actor or not subject:
            raise ValueError("audit event, actor, and subject must be non-empty")
        event_id = (event_id or uuid4().hex).strip()
        if (
            not event_id
            or len(event_id) > 240
            or any(ord(char) < 32 for char in event_id)
        ):
            raise ValueError("audit event_id must be 1-240 printable characters")
        safe_details = redact_secrets(details)
        bounded_json_dumps(safe_details, max_bytes=256 * 1024)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock:
            entries = self._entries_unlocked()
            previous_hash, _, _, chain_error, _ = self._verify_entries(entries)
            if chain_error is not None:
                raise ValueError(
                    f"refusing to append to invalid audit ledger: {chain_error}"
                )
            self._refuse_on_anchor_mismatch(entries, previous_hash)
            for existing in entries:
                if existing.get("event_id") != event_id:
                    continue
                comparable = {
                    "event": event[:120],
                    "actor": actor[:120],
                    "subject": subject[:200],
                    "details": safe_details,
                }
                if all(existing.get(key) == value for key, value in comparable.items()):
                    return existing
                raise ValueError(
                    "audit event_id was already used for different event content"
                )
            if len(entries) >= MAX_AUDIT_ENTRIES:
                raise ValueError("audit ledger entry limit reached")
            body: dict[str, Any] = {
                "sequence": len(entries) + 1,
                "event_id": event_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event[:120],
                "actor": actor[:120],
                "subject": subject[:200],
                "details": safe_details,
                "previous_hash": previous_hash,
            }
            if self._active_key_id is not None:
                body["entry_key_id"] = self._active_key_id
            body["entry_hash"] = hashlib.sha256(
                previous_hash.encode("ascii") + _canonical(body)
            ).hexdigest()
            if self._active_key_id is not None:
                body["entry_mac"] = self._mac("audit-entry-v1", body)
            append_jsonl(self.path, body)
            if self._active_key_id is not None:
                self._write_anchor(
                    sequence=int(body["sequence"]), head=str(body["entry_hash"])
                )
            self._invalidate_verified_cache()
            return body

    def append_events(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Append many audit events in one locked, chain-verified pass.

        Semantically equivalent to calling ``append`` once per record, but the
        ledger is read and its hash chain verified once instead of once per
        record.  Idempotency is preserved per event: an ``event_id`` already
        present with identical content is skipped and its stored entry returned,
        while a reused ``event_id`` with different content raises.  Returns one
        body per input record, in order.
        """
        if not records:
            return []
        # Validate every record before touching the file, mirroring ``append``.
        prepared: list[tuple[str, str, str, str, dict[str, Any]]] = []
        for record in records:
            event = str(record.get("event", "")).strip()
            actor = str(record.get("actor", "")).strip()
            subject = str(record.get("subject", "")).strip()
            if not event or not actor or not subject:
                raise ValueError("audit event, actor, and subject must be non-empty")
            event_id = str(record.get("event_id") or uuid4().hex).strip()
            if (
                not event_id
                or len(event_id) > 240
                or any(ord(char) < 32 for char in event_id)
            ):
                raise ValueError("audit event_id must be 1-240 printable characters")
            safe_details = redact_secrets(dict(record.get("details") or {}))
            bounded_json_dumps(safe_details, max_bytes=256 * 1024)
            prepared.append((event_id, event, actor, subject, safe_details))

        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock:
            entries = self._entries_unlocked()
            previous_hash, _, _, chain_error, _ = self._verify_entries(entries)
            if chain_error is not None:
                raise ValueError(
                    f"refusing to append to invalid audit ledger: {chain_error}"
                )
            self._refuse_on_anchor_mismatch(entries, previous_hash)
            by_id = {str(entry.get("event_id")): entry for entry in entries}
            results: list[dict[str, Any]] = []
            appended: list[dict[str, Any]] = []
            for event_id, event, actor, subject, safe_details in prepared:
                comparable = {
                    "event": event[:120],
                    "actor": actor[:120],
                    "subject": subject[:200],
                    "details": safe_details,
                }
                existing = by_id.get(event_id)
                if existing is not None:
                    if not all(
                        existing.get(key) == value for key, value in comparable.items()
                    ):
                        raise ValueError(
                            "audit event_id was already used for different event content"
                        )
                    results.append(existing)
                    continue
                if len(entries) + len(appended) >= MAX_AUDIT_ENTRIES:
                    raise ValueError("audit ledger entry limit reached")
                body: dict[str, Any] = {
                    "sequence": len(entries) + len(appended) + 1,
                    "event_id": event_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": event[:120],
                    "actor": actor[:120],
                    "subject": subject[:200],
                    "details": safe_details,
                    "previous_hash": previous_hash,
                }
                if self._active_key_id is not None:
                    body["entry_key_id"] = self._active_key_id
                body["entry_hash"] = hashlib.sha256(
                    previous_hash.encode("ascii") + _canonical(body)
                ).hexdigest()
                if self._active_key_id is not None:
                    body["entry_mac"] = self._mac("audit-entry-v1", body)
                previous_hash = str(body["entry_hash"])
                by_id[event_id] = body
                appended.append(body)
                results.append(body)
            if appended:
                payload = "".join(
                    json.dumps(
                        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    )
                    + "\n"
                    for body in appended
                )
                if len(payload.encode("utf-8")) > MAX_JSONL_RECORD_BYTES * max(
                    len(appended), 1
                ):
                    raise ValueError("audit ledger batch exceeds its record byte limit")
                with FileLock(str(self.path) + ".lock"):
                    if self.path.exists():
                        _reject_symlink(self.path)
                    created = not self.path.exists()
                    with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                    try:
                        self.path.chmod(0o600)
                    except OSError:
                        pass
                    if created:
                        _fsync_directory(self.path.parent)
                if self._active_key_id is not None:
                    self._write_anchor(
                        sequence=int(appended[-1]["sequence"]),
                        head=str(appended[-1]["entry_hash"]),
                    )
            self._invalidate_verified_cache()
            return results

    def verify(self) -> dict[str, Any]:
        with self.lock:
            return self._verify_unlocked()

    def _file_signature(self) -> tuple[float, int] | None:
        """Identity of the ledger file on disk, or None if it does not exist."""
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _invalidate_verified_cache(self) -> None:
        """Drop the cached verify() result; called after every append."""
        self._verified_signature = None
        self._verified_cache = None

    def _verify_unlocked(self) -> dict[str, Any]:
        # A caller that verifies after every append makes the chain re-read
        # O(N) cost become O(N^2). If the file has not changed since the last
        # verified append, the cached verdict is still authoritative: the
        # chain is append-only and this process holds the lock.
        signature = self._file_signature()
        if (
            signature is not None
            and self._verified_signature == signature
            and self._verified_cache is not None
        ):
            return self._verified_cache
        result = self._compute_verified()
        # Only cache healthy verdicts; a failure may be a transient read error
        # (torn final line, concurrent writer) that a retry would resolve.
        if result.get("valid"):
            self._verified_signature = signature
            self._verified_cache = result
        return result

    def _compute_verified(self) -> dict[str, Any]:
        try:
            entries = self._entries_unlocked()
        except ValueError as exc:
            return {
                "valid": False,
                "chain_valid": False,
                "sealed": False,
                "entries": 0,
                "error": str(exc),
            }

        previous_hash, sealed_sequences, key_ids_used, error, chain_valid = (
            self._verify_entries(entries)
        )
        if error is not None:
            return self._failure(entries, error, chain_valid=chain_valid)

        anchor_status = self._verify_anchor(sequence=len(entries), head=previous_hash)
        if not anchor_status["valid"]:
            return {
                "valid": False,
                "chain_valid": True,
                "sealed": False,
                "entries": len(entries),
                "head": previous_hash,
                "mode": "chain_only" if not sealed_sequences else "partially_sealed",
                "active_key_id": self._active_key_id,
                "key_ids_used": sorted(key_ids_used),
                "error": anchor_status["error"],
            }

        if anchor_status.get("key_id"):
            key_ids_used.add(str(anchor_status["key_id"]))
        all_entries_macd = bool(entries) and len(sealed_sequences) == len(entries)
        anchored = bool(entries) and bool(anchor_status["sealed"])
        if all_entries_macd and anchored:
            mode = "sealed"
        elif anchored:
            mode = "sealed_with_legacy_prefix"
        elif sealed_sequences:
            mode = "partially_sealed"
        else:
            mode = "chain_only"
        return {
            "valid": True,
            "chain_valid": True,
            "sealed": anchored,
            "entries": len(entries),
            "head": previous_hash,
            "mode": mode,
            "sealed_from_sequence": min(sealed_sequences) if sealed_sequences else None,
            "anchor_present": anchor_status["present"],
            "anchor_valid": anchor_status["valid"],
            "active_key_id": self._active_key_id,
            "key_ids_used": sorted(key_ids_used),
        }

    def _verify_anchor(self, *, sequence: int, head: str) -> dict[str, Any]:
        if not self.anchor_path.exists():
            if self._keys and sequence > 0:
                return {
                    "valid": False,
                    "sealed": False,
                    "present": False,
                    "error": "sealing key is configured but audit anchor is missing",
                }
            return {"valid": True, "sealed": False, "present": False, "key_id": None}
        if not self._keys:
            return {
                "valid": False,
                "sealed": False,
                "present": True,
                "error": "audit anchor exists but audit HMAC keys are unavailable",
            }
        try:
            anchor = read_json(self.anchor_path, {})
        except ValueError as exc:
            return {"valid": False, "sealed": False, "present": True, "error": str(exc)}
        if not isinstance(anchor, dict) or anchor.get("schema_version") not in {1, 2}:
            return {
                "valid": False,
                "sealed": False,
                "present": True,
                "error": "unsupported audit anchor schema",
            }
        candidate = dict(anchor)
        actual_mac = candidate.pop("anchor_mac", None)
        matched = self._matching_key_id(
            "audit-anchor-v1",
            candidate,
            actual_mac,
            declared_key_id=candidate.get("key_id"),
        )
        if matched is None:
            if candidate.get("key_id") not in (None, *self._keys.keys()):
                error = "audit anchor references an unavailable key ID"
            else:
                error = "audit anchor MAC mismatch"
            return {"valid": False, "sealed": False, "present": True, "error": error}
        if candidate.get("sequence") != sequence or candidate.get("head") != head:
            return {
                "valid": False,
                "sealed": False,
                "present": True,
                "error": "audit anchor does not match ledger head; truncation or replacement detected",
            }
        return {"valid": True, "sealed": True, "present": True, "key_id": matched}

    def _refuse_on_anchor_mismatch(
        self, entries: list[dict[str, Any]], previous_hash: str
    ) -> None:
        """Fail closed before appending when a present seal no longer matches.

        A missing anchor is legal: the ledger may be chain-only, or sealing may
        be newly configured over an existing chain.  An anchor that *is* present
        but no longer matches the chain head, however, is tamper evidence — the
        sealed tail was truncated or replaced.  Refusing here preserves that
        evidence instead of letting the next append silently re-pin the anchor.
        """
        anchor_status = self._verify_anchor(sequence=len(entries), head=previous_hash)
        if not anchor_status["valid"] and anchor_status.get("present"):
            raise ValueError(
                "refusing to append to invalid audit ledger: "
                f"{anchor_status['error']}"
            )

    @staticmethod
    def _failure(
        entries: list[dict[str, Any]],
        error: str,
        *,
        chain_valid: bool = False,
    ) -> dict[str, Any]:
        return {
            "valid": False,
            "chain_valid": chain_valid,
            "sealed": False,
            "entries": len(entries),
            "error": error,
        }

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.lock:
            return self._entries_unlocked()[-limit:]
