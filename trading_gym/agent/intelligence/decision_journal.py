"""Durable decision attribution; financial truth remains in the account ledger.

One immutable admission record joins configured policy, actual source, bounded
intent and later order/fill/account event identifiers. SQLite transactions give
single-host idempotency, not exchange exactly-once execution or authorization.
No raw prompt, observation, credential or model rationale is persisted here.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Iterator

from trading_gym.agent.intelligence.types import DecisionSignal

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,299}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_KINDS = frozenset(
    {
        "intent",
        "order",
        "fill",
        "mark",
        "financing",
        "settlement",
        "blocked",
        "advice",
        "advice_revocation",
    }
)
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "secret",
        "password",
        "prompt",
        "messages",
        "observation",
        "rationale",
    }
)


class DecisionJournalConflict(PermissionError):
    """One identity was reused for different evidence; fail before submitting."""


def _id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid attribution identity")
    return value


def _time(value: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("timestamp must be finite UTC epoch seconds")
    return float(value)


def canonical_digest(value: Any) -> str:
    """Hash a caller's already admitted serializable value, without saving it."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _document(value: dict[str, Any]) -> tuple[str, str]:
    def check(item: Any, depth: int = 0) -> None:
        if depth > 8:
            raise ValueError("event nesting exceeds budget")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or key.lower() in _SECRET_KEYS:
                    raise ValueError(
                        "raw sensitive/model content is not an attribution field"
                    )
                check(child, depth + 1)
        elif isinstance(item, (tuple, list)):
            for child in item:
                check(child, depth + 1)

    check(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > 65_536:
        raise ValueError("attribution event exceeds byte budget")
    return encoded, hashlib.sha256(encoded.encode()).hexdigest()


class DecisionJournal:
    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        if self.path.is_symlink():
            raise PermissionError("decision journal cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    recorded_at REAL NOT NULL,
                    document TEXT NOT NULL,
                    sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
                    kind TEXT NOT NULL,
                    occurred_at REAL NOT NULL,
                    recorded_at REAL NOT NULL,
                    document TEXT NOT NULL,
                    sha256 TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS by_decision ON events(decision_id, sequence);
            """
            )
        self.path.chmod(0o600)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5.0)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _read(
        document: str, digest: str, recorded_at: float | None = None
    ) -> dict[str, Any]:
        if hashlib.sha256(document.encode()).hexdigest() != digest:
            raise DecisionJournalConflict("attribution digest mismatch")
        value = json.loads(document)
        if recorded_at is not None:
            value["recorded_at"] = recorded_at
        return value

    def record_decision(
        self,
        *,
        decision_id: str,
        run_id: str,
        account_id: str,
        mode: str,
        configured_backend: str,
        signal: DecisionSignal | None,
        effective_target: float | None,
        observed_at: float,
        context_sha256: str,
        model_bundle_id: str,
        failures: tuple[str, ...] = (),
        status: str = "admitted",
    ) -> bool:
        for identity in (
            decision_id,
            run_id,
            account_id,
            configured_backend,
            model_bundle_id,
        ):
            _id(identity)
        if mode not in {"paper", "live", "research"} or status not in {
            "admitted",
            "blocked",
        }:
            raise ValueError("invalid decision mode or admission status")
        if not isinstance(context_sha256, str) or not _SHA.fullmatch(context_sha256):
            raise ValueError("context must be represented by a SHA-256 digest")
        timestamp = _time(observed_at)
        if status == "admitted":
            if (
                not isinstance(signal, DecisionSignal)
                or type(effective_target) not in (int, float)
                or not math.isfinite(effective_target)
            ):
                raise ValueError(
                    "admitted decision requires a typed signal and finite effective target"
                )
        elif signal is not None or effective_target is not None:
            raise ValueError("blocked decisions cannot carry an executable target")
        if len(failures) > 32 or any(
            not isinstance(x, str) or len(x) > 200 for x in failures
        ):
            raise ValueError("invalid bounded failure taxonomy")
        advice = None
        if signal is not None:
            advice = asdict(signal)
            advice["action_kind"] = signal.action_kind.value
            advice["rationale_sha256"] = hashlib.sha256(
                advice.pop("rationale").encode()
            ).hexdigest()
        document, digest = _document(
            {
                "schema_version": 1,
                "decision_id": decision_id,
                "run_id": run_id,
                "account_id": account_id,
                "mode": mode,
                "configured_backend": configured_backend,
                "signal": advice,
                "effective_target": effective_target,
                "observed_at": timestamp,
                "context_sha256": context_sha256,
                "model_bundle_id": model_bundle_id,
                "failures": list(failures),
                "status": status,
            }
        )
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT document,sha256 FROM decisions WHERE decision_id=?",
                (decision_id,),
            ).fetchone()
            if prior:
                self._read(*prior)
                if prior[0] != document:
                    raise DecisionJournalConflict("decision identity changed")
                return False
            db.execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?)",
                (
                    decision_id,
                    account_id,
                    timestamp,
                    _time(self._clock()),
                    document,
                    digest,
                ),
            )
        return True

    def append_event(
        self,
        *,
        decision_id: str,
        event_id: str,
        kind: str,
        occurred_at: float,
        payload: dict[str, Any],
    ) -> bool:
        _id(decision_id)
        _id(event_id)
        if kind not in _KINDS or not isinstance(payload, dict):
            raise ValueError("invalid attribution event kind or payload")
        timestamp = _time(occurred_at)
        document, digest = _document(
            {
                "event_id": event_id,
                "decision_id": decision_id,
                "kind": kind,
                "occurred_at": timestamp,
                "payload": payload,
            }
        )
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT document,sha256 FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if prior:
                self._read(*prior)
                if prior[0] != document:
                    raise DecisionJournalConflict("event identity changed")
                return False
            parent = db.execute(
                "SELECT document,sha256 FROM decisions WHERE decision_id=?",
                (decision_id,),
            ).fetchone()
            if parent is None:
                raise DecisionJournalConflict("event has no admitted decision identity")
            decision = self._read(*parent)
            if decision["status"] == "blocked" and kind in {"intent", "order", "fill"}:
                raise DecisionJournalConflict(
                    "blocked decision cannot originate an order"
                )
            if (
                payload.get("account_id", decision["account_id"])
                != decision["account_id"]
            ):
                raise DecisionJournalConflict("event belongs to another account")
            db.execute(
                "INSERT INTO events(event_id,decision_id,kind,occurred_at,recorded_at,document,sha256) VALUES (?,?,?,?,?,?,?)",
                (
                    event_id,
                    decision_id,
                    kind,
                    timestamp,
                    _time(self._clock()),
                    document,
                    digest,
                ),
            )
        return True

    def decision(self, decision_id: str) -> dict[str, Any]:
        with self._connection() as db:
            row = db.execute(
                "SELECT document,sha256,recorded_at FROM decisions WHERE decision_id=?",
                (_id(decision_id),),
            ).fetchone()
        if row is None:
            raise KeyError(decision_id)
        return self._read(*row)

    def decisions(
        self, *, account_id: str | None = None, as_of: float | None = None
    ) -> tuple[dict[str, Any], ...]:
        if as_of is not None:
            _time(as_of)
        with self._connection() as db:
            rows = db.execute(
                "SELECT document,sha256,recorded_at FROM decisions WHERE (? IS NULL OR account_id=?) AND (? IS NULL OR (observed_at<=? AND recorded_at<=?)) ORDER BY observed_at,decision_id",
                (account_id, account_id, as_of, as_of, as_of),
            ).fetchall()
        return tuple(self._read(*row) for row in rows)

    def events(
        self,
        decision_id: str | None = None,
        *,
        kind: str | None = None,
        as_of: float | None = None,
    ) -> tuple[dict[str, Any], ...]:
        if as_of is not None:
            _time(as_of)
        with self._connection() as db:
            rows = db.execute(
                "SELECT document,sha256,recorded_at FROM events WHERE (? IS NULL OR decision_id=?) AND (? IS NULL OR kind=?) AND (? IS NULL OR (occurred_at<=? AND recorded_at<=?)) ORDER BY sequence",
                (decision_id, decision_id, kind, kind, as_of, as_of, as_of),
            ).fetchall()
        return tuple(self._read(*row) for row in rows)
