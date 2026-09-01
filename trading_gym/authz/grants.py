"""Delegated capability grants with a hash-chained ledger (ADR-028).

A wildcard grant delegates authority the issuing principal already holds.
The grant itself is born through the normal governance proposal path;
spending it is a local, fast, fail-closed check. This module provides the
data model, the evaluation function, and the durable journal.

Design notes:

- Constraint values are caller-supplied numbers (observed notional, order
  rate, drawdown). This module never reads the accounting ledger itself;
  composition stays at the API/coordinator layer where the existing auth
  chain (API key, actor principal) is already verified first.
- The ledger is a hash-chained JSONL journal: every record embeds the hash
  of its predecessor and of its own canonical payload, and a small anchor
  file pins the expected chain length, so truncation and in-place edits
  are detected when the ledger is opened.
- Evaluation is deny-by-default and fail-closed: malformed scopes, missing
  fields, and non-numeric constraint ceilings become denials or explicit
  errors, never silent allows.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from trading_gym.authz.scopes import ScopeError, ScopeSet
from trading_gym.common.persistence import append_jsonl, atomic_write_json

__all__ = [
    "ConstraintViolation",
    "Grant",
    "GrantDecision",
    "GrantLedger",
    "GrantLedgerIntegrityError",
    "GrantRequest",
    "GrantState",
    "ScopeDenied",
]

_GENESIS_HASH = "0" * 64
_PRINCIPAL_RE = r"^[a-z0-9][a-z0-9._-]{2,63}$"
_PRINCIPAL_COMPILED = re.compile(_PRINCIPAL_RE)
_MAX_SCOPES_PER_GRANT = 32
_MAX_TTL_HOURS = 24 * 365 * 5
_ANCHOR_SEQ = "seq"
_ANCHOR_HEAD = "head"


def _canonical(value: Mapping[str, Any]) -> bytes:
    """Canonical JSON encoding, matching the governance ledger convention."""

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class GrantLedgerIntegrityError(RuntimeError):
    """The grants journal is missing, reordered, edited, or truncated."""


class ConstraintViolation(ValueError):
    """A constraint ceiling or observed value is malformed for evaluation."""


class ScopeDenied(PermissionError):
    """Evaluation denied the requested scope for this principal."""


class GrantState:
    """Lifecycle states; string values match the journal encoding."""

    ACTIVE = "active"
    REVOKED = "revoked"
    REVOKED_BREACH = "revoked-breach"

    _ALL = (ACTIVE, REVOKED, REVOKED_BREACH)


@dataclass(frozen=True)
class GrantRequest:
    """Input to ``GrantLedger.issue``; validation happens there."""

    principal: str
    scopes: tuple[str, ...]
    ttl_hours: int
    issued_at: str
    constraints: tuple[tuple[str, float], ...] = ()
    issued_by: str = "system"


@dataclass(frozen=True)
class GrantDecision:
    """Result of evaluating one scope request against one grant."""

    allowed: bool
    reason: str
    grant_id: str
    matched_scope: str | None = None


@dataclass(frozen=True)
class Grant:
    """One delegated capability bundle, immutable once recorded."""

    grant_id: str
    principal: str
    scopes: ScopeSet
    constraints: Mapping[str, float]
    state: str
    issued_at: str
    expires_at: str
    issued_by: str
    _ledger: "GrantLedger | None" = field(default=None, compare=False, repr=False)

    # -- evaluation ---------------------------------------------------------

    def is_active(self, *, now: str | None = None) -> bool:
        if self.state != GrantState.ACTIVE:
            return False
        moment = _parse_iso(now) if now is not None else _parse_iso(self.issued_at)
        return moment < _parse_iso(self.expires_at)

    def evaluate(
        self,
        scope: str,
        *,
        now: str | None = None,
        constraints: Mapping[str, float] | None = None,
        breach: bool = False,
    ) -> GrantDecision:
        """Evaluate one concrete scope request against this grant.

        ``constraints`` carries caller-observed values (notional, rates).
        A breach decision requires the owning ledger so the latch is
        journaled; a detached grant refuses to evaluate a breach rather
        than silently skipping the record.
        """

        if not self.is_active(now=now):
            state = self.state
            if state == GrantState.ACTIVE:
                return GrantDecision(
                    allowed=False,
                    reason="grant_expired",
                    grant_id=self.grant_id,
                )
            reason = (
                "grant_revoked_breach"
                if state == GrantState.REVOKED_BREACH
                else "grant_revoked"
            )
            return GrantDecision(
                allowed=False, reason=reason, grant_id=self.grant_id
            )

        try:
            matched = self.scopes.match(scope)
        except ScopeError:
            return GrantDecision(
                allowed=False, reason="invalid_scope", grant_id=self.grant_id
            )
        if matched is None:
            return GrantDecision(
                allowed=False, reason="no_matching_scope", grant_id=self.grant_id
            )

        if constraints:
            for name, observed in constraints.items():
                ceiling = self.constraints.get(name)
                if ceiling is None:
                    continue
                if not isinstance(ceiling, (int, float)) or isinstance(
                    ceiling, bool
                ):
                    raise ConstraintViolation(
                        f"constraint ceiling {name!r} is not numeric"
                    )
                if not isinstance(observed, (int, float)) or isinstance(
                    observed, bool
                ):
                    raise ConstraintViolation(
                        f"observed value for {name!r} must be numeric"
                    )
                if observed > ceiling:
                    if breach:
                        self._latch_breach()
                    return GrantDecision(
                        allowed=False,
                        reason=f"constraint_{name}",
                        grant_id=self.grant_id,
                    )

        if breach:
            self._latch_breach()

        return GrantDecision(
            allowed=True,
            reason="ok",
            grant_id=self.grant_id,
            matched_scope=matched.text,
        )

    def _latch_breach(self) -> None:
        if self._ledger is None:
            raise RuntimeError(
                "breach latching requires the owning GrantLedger"
            )
        self._ledger._latch_breach(self.grant_id)

    # -- serialization ------------------------------------------------------

    def to_record(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "principal": self.principal,
            "scopes": [p.text for p in self.scopes.patterns],
            "constraints": dict(self.constraints),
            "state": self.state,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "issued_by": self.issued_by,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "Grant":
        required = (
            "grant_id",
            "principal",
            "scopes",
            "state",
            "issued_at",
            "expires_at",
        )
        for key in required:
            if key not in record:
                raise GrantLedgerIntegrityError(
                    f"grant record missing {key!r}"
                )
        state = record["state"]
        if state not in GrantState._ALL:
            raise GrantLedgerIntegrityError(f"unknown grant state {state!r}")
        scopes = ScopeSet.parse(tuple(record["scopes"]))
        constraints = dict(record.get("constraints") or {})
        return cls(
            grant_id=str(record["grant_id"]),
            principal=str(record["principal"]),
            scopes=scopes,
            constraints=constraints,
            state=str(state),
            issued_at=str(record["issued_at"]),
            expires_at=str(record["expires_at"]),
            issued_by=str(record.get("issued_by", "system")),
        )


@dataclass(frozen=True)
class _LedgerEvent:
    seq: int
    type: str
    ts: str
    prev_hash: str
    entry_hash: str
    payload: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        body = {
            "seq": self.seq,
            "type": self.type,
            "ts": self.ts,
            "prev_hash": self.prev_hash,
            **self.payload,
        }
        return {**body, "entry_hash": self.entry_hash}


class GrantLedger:
    """Append-only, hash-chained journal of grant mutations."""

    def __init__(self, path: Path, *, verify: bool = True) -> None:
        self._path = Path(path)
        self._anchor_path = self._path.with_suffix(self._path.suffix + ".anchor")
        self._events: list[_LedgerEvent] = []
        self._grants: dict[str, Grant] = {}
        self._load(verify=verify)

    # -- construction -------------------------------------------------------

    @classmethod
    def open(cls, path: Path | str, *, verify: bool = True) -> "GrantLedger":
        return cls(Path(path), verify=verify)

    def _load(self, *, verify: bool) -> None:
        if not self._path.exists():
            return
        raw_lines = self._path.read_text(encoding="utf-8").splitlines()
        for line in raw_lines:
            if not line.strip():
                raise GrantLedgerIntegrityError(
                    "blank line in grants journal"
                )
            record = json.loads(line)
            self._events.append(self._decode_event(record))
        if verify:
            self._verify_chain()
            self._verify_anchor()
        self._replay()

    def _replay(self) -> None:
        """Rebuild current grant state from the verified journal.

        Replay is structural: an ``issued`` event restores the recorded
        grant, ``revoked`` and ``breach`` events advance its state. A
        structurally valid but semantically malformed record (unknown
        state, ungrantable scope) fails the open — a journal that cannot
        be interpreted must not yield a half-trusted state.
        """

        for event in self._events:
            if event.type == "issued":
                record = event.payload.get("grant")
                if not isinstance(record, Mapping):
                    raise GrantLedgerIntegrityError(
                        "issued event carries no grant record"
                    )
                try:
                    grant = Grant.from_record(record)
                except (ScopeError, GrantLedgerIntegrityError) as exc:
                    raise GrantLedgerIntegrityError(
                        f"malformed grant record at seq {event.seq}: {exc}"
                    ) from exc
                self._grants[grant.grant_id] = replace(
                    grant, _ledger=self
                )
            elif event.type == "revoked":
                grant_id = str(event.payload.get("grant_id", ""))
                current: Grant | None = self._grants.get(grant_id)
                if current is None:
                    raise GrantLedgerIntegrityError(
                        f"revoked event references unknown grant at seq {event.seq}"
                    )
                self._grants[grant_id] = replace(
                    current, state=GrantState.REVOKED
                )
            elif event.type == "breach":
                grant_id = str(event.payload.get("grant_id", ""))
                latched: Grant | None = self._grants.get(grant_id)
                if latched is None:
                    raise GrantLedgerIntegrityError(
                        f"breach event references unknown grant at seq {event.seq}"
                    )
                self._grants[grant_id] = replace(
                    latched, state=GrantState.REVOKED_BREACH
                )
            else:
                raise GrantLedgerIntegrityError(
                    f"unknown journal event type {event.type!r} at seq {event.seq}"
                )

    def _decode_event(self, record: Mapping[str, Any]) -> _LedgerEvent:
        for key in ("seq", "type", "ts", "prev_hash", "entry_hash"):
            if key not in record:
                raise GrantLedgerIntegrityError(
                    f"journal record missing {key!r}"
                )
        body = {
            k: v
            for k, v in record.items()
            if k not in ("entry_hash",)
        }
        computed = hashlib.sha256(_canonical(body)).hexdigest()
        if computed != record["entry_hash"]:
            raise GrantLedgerIntegrityError(
                f"journal record {record['seq']} hash mismatch"
            )
        payload = {
            k: v for k, v in record.items() if k not in ("seq", "type", "ts", "prev_hash", "entry_hash")
        }
        return _LedgerEvent(
            seq=int(record["seq"]),
            type=str(record["type"]),
            ts=str(record["ts"]),
            prev_hash=str(record["prev_hash"]),
            entry_hash=str(record["entry_hash"]),
            payload=payload,
        )

    def _verify_chain(self) -> None:
        previous = _GENESIS_HASH
        for index, event in enumerate(self._events, start=1):
            if event.seq != index:
                raise GrantLedgerIntegrityError(
                    f"journal sequence gap at {event.seq}"
                )
            if event.prev_hash != previous:
                raise GrantLedgerIntegrityError(
                    f"journal chain break at seq {event.seq}"
                )
            previous = event.entry_hash

    def _verify_anchor(self) -> None:
        if not self._events:
            return
        expected_seq = self._events[-1].seq
        expected_head = self._events[-1].entry_hash
        if not self._anchor_path.exists():
            raise GrantLedgerIntegrityError(
                "grants journal anchor missing for non-empty journal"
            )
        anchor = json.loads(self._anchor_path.read_text(encoding="utf-8"))
        if (
            anchor.get(_ANCHOR_SEQ) != expected_seq
            or anchor.get(_ANCHOR_HEAD) != expected_head
        ):
            raise GrantLedgerIntegrityError(
                "grants journal anchor mismatch (truncation or divergence)"
            )

    def _append(self, event_type: str, ts: str, payload: dict[str, Any]) -> None:
        seq = (self._events[-1].seq if self._events else 0) + 1
        prev_hash = self._events[-1].entry_hash if self._events else _GENESIS_HASH
        body = {
            "seq": seq,
            "type": event_type,
            "ts": ts,
            "prev_hash": prev_hash,
            **payload,
        }
        entry_hash = hashlib.sha256(_canonical(body)).hexdigest()
        event = _LedgerEvent(
            seq=seq,
            type=event_type,
            ts=ts,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            payload=payload,
        )
        append_jsonl(self._path, event.to_json())
        self._events.append(event)
        self._write_anchor()

    def _write_anchor(self) -> None:
        atomic_write_json(
            self._anchor_path,
            {
                _ANCHOR_SEQ: self._events[-1].seq,
                _ANCHOR_HEAD: self._events[-1].entry_hash,
            },
        )

    # -- mutations ----------------------------------------------------------

    def issue(
        self, request: GrantRequest, *, at: str | None = None
    ) -> Grant:
        issued_at = _parse_iso(at or request.issued_at)
        if not _PRINCIPAL_COMPILED.match(request.principal):
            raise ValueError(
                f"invalid principal {request.principal!r}"
            )
        if not isinstance(request.ttl_hours, int) or request.ttl_hours <= 0:
            raise ValueError("ttl_hours must be a positive integer")
        if request.ttl_hours > _MAX_TTL_HOURS:
            raise ValueError(
                f"ttl_hours exceeds the {_MAX_TTL_HOURS}-hour ceiling"
            )
        scopes = ScopeSet.parse(tuple(request.scopes))
        if not scopes.patterns:
            raise ScopeError("grant must carry at least one scope pattern")
        if len(scopes.patterns) > _MAX_SCOPES_PER_GRANT:
            raise ValueError(
                f"grant exceeds {_MAX_SCOPES_PER_GRANT} scope patterns"
            )
        grant_id = "gw-" + uuid.uuid4().hex[:12]
        while grant_id in self._grants:
            grant_id = "gw-" + uuid.uuid4().hex[:12]
        expires_at = issued_at + timedelta(hours=request.ttl_hours)
        constraints = {k: v for k, v in request.constraints}
        grant = Grant(
            grant_id=grant_id,
            principal=request.principal,
            scopes=scopes,
            constraints=constraints,
            state=GrantState.ACTIVE,
            issued_at=_format_iso(issued_at),
            expires_at=_format_iso(expires_at),
            issued_by=request.issued_by,
            _ledger=self,
        )
        self._append(
            "issued",
            _format_iso(issued_at),
            {"grant": grant.to_record()},
        )
        self._grants[grant.grant_id] = grant
        return grant

    def revoke(
        self,
        grant_id: str,
        *,
        actor: str = "system",
        reason: str = "manual",
        at: str | None = None,
    ) -> Grant:
        grant = self._grants.get(grant_id)
        if grant is None:
            raise KeyError(f"unknown grant {grant_id!r}")
        if grant.state != GrantState.ACTIVE:
            raise ValueError(
                f"grant {grant_id} is {grant.state}, cannot revoke"
            )
        revoked = replace(grant, state=GrantState.REVOKED)
        self._append(
            "revoked",
            _format_iso(_parse_iso(at) if at else _now_utc()),
            {
                "grant_id": grant_id,
                "actor": actor,
                "reason": reason,
            },
        )
        self._grants[grant_id] = revoked
        return revoked

    def _latch_breach(self, grant_id: str) -> None:
        grant = self._grants.get(grant_id)
        if grant is None or grant.state != GrantState.ACTIVE:
            return
        latched = replace(grant, state=GrantState.REVOKED_BREACH)
        self._append(
            "breach",
            _format_iso(_now_utc()),
            {"grant_id": grant_id},
        )
        self._grants[grant_id] = latched

    # -- queries ------------------------------------------------------------

    def get(self, grant_id: str) -> Grant:
        return self._grants[grant_id]

    def active_grants(
        self, principal: str | None = None, *, now: str | None = None
    ) -> tuple[Grant, ...]:
        return tuple(
            grant
            for grant in self._grants.values()
            if (principal is None or grant.principal == principal)
            and grant.is_active(now=now)
        )

    def check(
        self,
        principal: str,
        scope: str,
        *,
        now: str | None = None,
        constraints: Mapping[str, float] | None = None,
    ) -> GrantDecision:
        """Deny-by-default evaluation across every active grant."""

        for grant in self.active_grants(principal, now=now):
            try:
                decision = grant.evaluate(
                    scope, now=now, constraints=constraints
                )
            except ScopeError:
                return GrantDecision(
                    allowed=False, reason="invalid_scope", grant_id=""
                )
            if decision.allowed:
                return decision
        return GrantDecision(
            allowed=False, reason="no_matching_scope", grant_id=""
        )

    def require(
        self,
        principal: str,
        scope: str,
        *,
        now: str | None = None,
        constraints: Mapping[str, float] | None = None,
    ) -> Grant:
        """Like ``check`` but returns the authorising grant or raises."""

        for grant in self.active_grants(principal, now=now):
            decision = grant.evaluate(
                scope, now=now, constraints=constraints
            )
            if decision.allowed:
                return grant
        raise ScopeDenied(
            f"principal {principal!r} lacks scope {scope!r}"
        )

    # -- integrity ----------------------------------------------------------

    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(event.to_json() for event in self._events)

    def verify(self) -> dict[str, Any]:
        self._verify_chain()
        self._verify_anchor()
        return {"valid": True, "entries": len(self._events)}
