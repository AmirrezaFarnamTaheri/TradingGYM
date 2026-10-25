"""AXIOM-1 autonomy ladder (ADR-032): governed agent authority levels.

The ladder answers exactly one question — *what is this agent allowed to
do right now* — and changes the answer only through evidence-gated,
journaled transitions:

- **L0_SHADOW** decisions are journaled, nothing executes.
- **L1_PAPER** paper execution through the governed path.
- **L2_TINY_LIVE** live orders under tight grant constraints
  (``max_notional_quote`` et al. — the ADR-028 grant carries the numbers).
- **L3_SCALED** sustained operation with red-team findings closed.

Structural guarantees:

- **No skipping.** Promotion is only ever one rung; the gate table is
  keyed by (current level, next level).
- **Evidence-gated.** Each rung demands its own evidence fields; a gate
  that cannot verify its evidence denies with a named reason. Evidence
  is *asserted by the caller* at v1 (the operational data source —
  shadow window accounting, DSR reports, red-team registry) — the
  governor verifies shape and thresholds, and the journal makes every
  claim attributable to the caller that made it.
- **Immediate demotion.** Any breach drops the agent to shadow; after a
  demotion the shadow window restarts (``demoted_at`` timestamp gates
  the next promotion), so a violation costs the full window, not a
  keystroke.
- **Tamper-evident.** Every transition is hash-chained into
  ``TRADING_GYM_HOME/autonomy/autonomy.jsonl`` with the same canonical
  JSON + SHA-256 pattern as the grant ledger; an edited or truncated
  journal fails its open.

The governor grants no authority by itself: L2+ execution still flows
through the coordinator, wildcard grants, and four-eyes governance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from filelock import FileLock
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from trading_gym.core.persistence import append_jsonl, atomic_write_json

__all__ = [
    "AutonomyGovernor",
    "AutonomyLedgerIntegrityError",
    "AutonomyLevel",
    "PromotionDenied",
    "PromotionEvidence",
]

_GENESIS_HASH = "0" * 64

#: Minimum shadow window before any promotion is considered.
MIN_SHADOW_DAYS = 30
#: Minimum journaled decisions across the window.
MIN_SHADOW_DECISIONS = 200
#: Minimum DSR-positive fusion lift for live promotion.
MIN_FUSION_LIFT_DSR = 0.05


class AutonomyLedgerIntegrityError(RuntimeError):
    """The autonomy journal is missing, edited, reordered, or truncated."""


class PromotionDenied(PermissionError):
    """A promotion (or terminal-level) request failed its gate."""


class AutonomyLevel(str, Enum):
    L0_SHADOW = "L0_SHADOW"
    L1_PAPER = "L1_PAPER"
    L2_TINY_LIVE = "L2_TINY_LIVE"
    L3_SCALED = "L3_SCALED"

    @property
    def next(self) -> "AutonomyLevel | None":
        order = list(AutonomyLevel)
        index = order.index(self)
        return order[index + 1] if index + 1 < len(order) else None


@dataclass(frozen=True)
class PromotionEvidence:
    """Caller-asserted operational evidence for one promotion attempt.

    Fields beyond the common window/decision/breach triple are only
    consulted by the gates that need them; unknown-shaped values fail
    the gate that consumes them.
    """

    days_in_current_level: int
    decisions_at_level: int
    constraint_breaches: int
    journal_valid: bool
    fusion_lift_dsr: float | None = None
    critical_proposal_id: str = ""
    red_team_open_findings: int = 0
    slippage_within_tolerance: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.days_in_current_level, int) or isinstance(
            self.days_in_current_level, bool
        ):
            raise ValueError("days_in_current_level must be an integer")
        if self.days_in_current_level < 0:
            raise ValueError("days_in_current_level must be non-negative")
        if not isinstance(self.decisions_at_level, int) or isinstance(
            self.decisions_at_level, bool
        ):
            raise ValueError("decisions_at_level must be an integer")
        if self.decisions_at_level < 0:
            raise ValueError("decisions_at_level must be non-negative")
        if not isinstance(self.constraint_breaches, int) or isinstance(
            self.constraint_breaches, bool
        ):
            raise ValueError("constraint_breaches must be an integer")
        if self.constraint_breaches < 0:
            raise ValueError("constraint_breaches must be non-negative")
        if not isinstance(self.journal_valid, bool):
            raise ValueError("journal_valid must be a boolean")


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _gate_shadow_to_paper(
    evidence: PromotionEvidence, demoted_at: str | None, now: datetime
) -> None:
    if evidence.constraint_breaches > 0:
        raise PromotionDenied("constraint breaches in the shadow window")
    if not evidence.journal_valid:
        raise PromotionDenied("decision journal failed integrity verification")
    window_start = _parse_iso(demoted_at) if demoted_at is not None else None
    if window_start is not None:
        effective_days = (now - window_start).days
    else:
        effective_days = evidence.days_in_current_level
    if effective_days < MIN_SHADOW_DAYS:
        raise PromotionDenied(
            f"shadow window incomplete: {effective_days} < {MIN_SHADOW_DAYS} days"
        )
    if evidence.decisions_at_level < MIN_SHADOW_DECISIONS:
        raise PromotionDenied(
            f"insufficient decisions: {evidence.decisions_at_level} < "
            f"{MIN_SHADOW_DECISIONS}"
        )


def _gate_paper_to_live(
    evidence: PromotionEvidence, demoted_at: str | None, now: datetime
) -> None:
    _gate_shadow_to_paper(evidence, demoted_at, now)
    if (
        evidence.fusion_lift_dsr is None
        or evidence.fusion_lift_dsr < MIN_FUSION_LIFT_DSR
    ):
        raise PromotionDenied("positive DSR fusion lift required for live promotion")
    if not evidence.critical_proposal_id:
        raise PromotionDenied(
            "an approved critical proposal is required for live authority"
        )


def _gate_live_to_scaled(
    evidence: PromotionEvidence, demoted_at: str | None, now: datetime
) -> None:
    _gate_paper_to_live(evidence, demoted_at, now)
    if evidence.red_team_open_findings > 0:
        raise PromotionDenied("red-team findings must be closed for L3")
    if not evidence.slippage_within_tolerance:
        raise PromotionDenied("live-vs-backtest slippage must be within tolerance")


_GATES = {
    (AutonomyLevel.L0_SHADOW, AutonomyLevel.L1_PAPER): _gate_shadow_to_paper,
    (AutonomyLevel.L1_PAPER, AutonomyLevel.L2_TINY_LIVE): _gate_paper_to_live,
    (AutonomyLevel.L2_TINY_LIVE, AutonomyLevel.L3_SCALED): _gate_live_to_scaled,
}

_AUTHORITY = {
    AutonomyLevel.L0_SHADOW: {
        "live_orders": False,
        "paper_orders": False,
        "journaled_decisions": True,
        "self_revision": "none",
    },
    AutonomyLevel.L1_PAPER: {
        "live_orders": False,
        "paper_orders": True,
        "journaled_decisions": True,
        "self_revision": "none",
    },
    AutonomyLevel.L2_TINY_LIVE: {
        "live_orders": True,
        "paper_orders": True,
        "journaled_decisions": True,
        "self_revision": "parametric",
    },
    AutonomyLevel.L3_SCALED: {
        "live_orders": True,
        "paper_orders": True,
        "journaled_decisions": True,
        "self_revision": "parametric",
    },
}


class _Event:
    __slots__ = ("seq", "type", "ts", "prev_hash", "entry_hash", "payload")

    def __init__(
        self,
        seq: int,
        type: str,
        ts: str,
        prev_hash: str,
        payload: dict[str, Any],
    ) -> None:
        self.seq = seq
        self.type = type
        self.ts = ts
        self.prev_hash = prev_hash
        self.payload = payload
        body = {
            "seq": seq,
            "type": type,
            "ts": ts,
            "prev_hash": prev_hash,
            **payload,
        }
        self.entry_hash = hashlib.sha256(_canonical(body)).hexdigest()

    def to_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type,
            "ts": self.ts,
            "prev_hash": self.prev_hash,
            **self.payload,
            "entry_hash": self.entry_hash,
        }


class AutonomyGovernor:
    """Evidence-gated, journal-backed authority-level state machine."""

    def __init__(self, path: Path, *, verify: bool = True) -> None:
        self._path = Path(path)
        self._anchor_path = self._path.with_suffix(self._path.suffix + ".anchor")
        self._events: list[_Event] = []
        self._level = AutonomyLevel.L0_SHADOW
        self._level_since: str | None = None
        self._demoted_at: str | None = None
        # Chain lock held across load+verify+append, mirroring AuditLedger.
        # Without it two handles on one journal both derive seq/prev_hash from
        # state verified at construction, both append, and the on-disk chain
        # forks while both callers are told they succeeded.
        self._chain_lock = FileLock(str(self._path) + ".chain.lock")
        self._load(verify=verify)

    @classmethod
    def open(cls, path: Path | str, *, verify: bool = True) -> "AutonomyGovernor":
        return cls(Path(path), verify=verify)

    # -- state --------------------------------------------------------------

    @property
    def level(self) -> AutonomyLevel:
        return self._level

    def authority(self) -> dict[str, Any]:
        return dict(_AUTHORITY[self._level])

    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(event.to_json() for event in self._events)

    def verify(self) -> dict[str, Any]:
        previous = _GENESIS_HASH
        for index, event in enumerate(self._events, start=1):
            if event.seq != index or event.prev_hash != previous:
                raise AutonomyLedgerIntegrityError(
                    f"journal chain break at seq {event.seq}"
                )
            previous = event.entry_hash
        if self._events:
            anchor = json.loads(self._anchor_path.read_text(encoding="utf-8"))
            if (
                anchor.get("seq") != self._events[-1].seq
                or anchor.get("head") != self._events[-1].entry_hash
            ):
                raise AutonomyLedgerIntegrityError("journal anchor mismatch")
        return {"valid": True, "entries": len(self._events)}

    # -- transitions ---------------------------------------------------------

    def promote(
        self, evidence: PromotionEvidence, *, at: str | None = None
    ) -> "AutonomyGovernor":
        now = _parse_iso(at) if at else datetime.now(timezone.utc)
        nxt = self._level.next
        if nxt is None:
            raise PromotionDenied(f"already at the top level {self._level.value}")
        gate = _GATES[(self._level, nxt)]
        if self._level is AutonomyLevel.L0_SHADOW and evidence.critical_proposal_id:
            raise PromotionDenied(
                "a critical proposal cannot exist for a shadow-level agent "
                "(skip-level promotion attempt)"
            )
        gate(evidence, self._demoted_at, now)
        self._append(
            "promoted",
            now,
            {
                "from": self._level.value,
                "to": nxt.value,
                "evidence": {
                    "days_in_current_level": evidence.days_in_current_level,
                    "decisions_at_level": evidence.decisions_at_level,
                    "constraint_breaches": evidence.constraint_breaches,
                    "journal_valid": evidence.journal_valid,
                    "fusion_lift_dsr": evidence.fusion_lift_dsr,
                    "critical_proposal_id": evidence.critical_proposal_id,
                    "red_team_open_findings": evidence.red_team_open_findings,
                    "slippage_within_tolerance": evidence.slippage_within_tolerance,
                },
            },
        )
        self._level = nxt
        self._level_since = _format_iso(now)
        self._demoted_at = None
        return self

    def demote(
        self, *, reason: str, actor: str, at: str | None = None
    ) -> "AutonomyGovernor":
        if not reason or not actor:
            raise ValueError("demotion requires reason and actor")
        now = _parse_iso(at) if at else datetime.now(timezone.utc)
        self._append(
            "demoted",
            now,
            {
                "from": self._level.value,
                "to": AutonomyLevel.L0_SHADOW.value,
                "reason": reason,
                "actor": actor,
            },
        )
        self._level = AutonomyLevel.L0_SHADOW
        self._level_since = _format_iso(now)
        self._demoted_at = _format_iso(now)
        return self

    # -- journal -------------------------------------------------------------

    def _load(self, *, verify: bool) -> None:
        if not self._path.exists():
            return
        for number, line in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                raise AutonomyLedgerIntegrityError("blank line in autonomy journal")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                # A torn final write must surface as the module's integrity
                # error so callers trigger repair, not as a bare decode error.
                raise AutonomyLedgerIntegrityError(
                    f"autonomy journal has invalid JSON at line {number}"
                ) from exc
            for key in ("seq", "type", "ts", "prev_hash", "entry_hash"):
                if key not in record:
                    raise AutonomyLedgerIntegrityError(
                        f"journal record missing {key!r}"
                    )
            body = {key: value for key, value in record.items() if key != "entry_hash"}
            computed = hashlib.sha256(_canonical(body)).hexdigest()
            if computed != record["entry_hash"]:
                raise AutonomyLedgerIntegrityError(
                    f"journal record {record['seq']} hash mismatch"
                )
            payload = {
                key: value
                for key, value in record.items()
                if key not in ("seq", "type", "ts", "prev_hash", "entry_hash")
            }
            self._events.append(
                _Event(
                    seq=int(record["seq"]),
                    type=str(record["type"]),
                    ts=str(record["ts"]),
                    prev_hash=str(record["prev_hash"]),
                    payload=payload,
                )
            )
        if verify:
            self.verify()
        self._replay()

    def _replay(self) -> None:
        for event in self._events:
            if event.type == "promoted":
                self._level = AutonomyLevel(event.payload["to"])
                self._level_since = event.ts
                self._demoted_at = None
            elif event.type == "demoted":
                self._level = AutonomyLevel.L0_SHADOW
                self._level_since = event.ts
                self._demoted_at = event.ts
            else:
                raise AutonomyLedgerIntegrityError(
                    f"unknown autonomy event type {event.type!r}"
                )

    def _append(self, type: str, moment: datetime, payload: dict[str, Any]) -> None:
        # Hold the chain lock across re-read + verify + append so seq/prev_hash
        # come from the real file tail, not state verified at construction.
        with self._chain_lock:
            self._events = []
            self._level = AutonomyLevel.L0_SHADOW
            self._level_since = None
            self._demoted_at = None
            self._load(verify=True)
            seq = (self._events[-1].seq if self._events else 0) + 1
            prev_hash = self._events[-1].entry_hash if self._events else _GENESIS_HASH
            event = _Event(seq, type, _format_iso(moment), prev_hash, payload)
            append_jsonl(self._path, event.to_json())
            self._events.append(event)
            atomic_write_json(
                self._anchor_path,
                {"seq": self._events[-1].seq, "head": self._events[-1].entry_hash},
            )
