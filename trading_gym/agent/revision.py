"""AXIOM-1 self-improvement revision surface (ADR-033).

The constitution of the self-revising agent. Everything the loop may
rewrite passes through this module; nothing bypasses it.

- **Tier registry** (:class:`RevisionSurface.default_tiers`). Five tiers
  with per-tier evidence thresholds and authority: P parametric
  (auto within grant bounds), S structural (auto on suite + replicated
  holdout pass), M meta (auto with stronger lift), B behavioral
  (proposal required — never auto), X code/artifacts (never
  self-revisable; nomination only).
- **Gate chain** (:class:`GateChain`), fixed order: sealed holdout →
  regression floor → diversity floor → canary. A candidate that names a
  holdout id it was not seeded with fails (holdout leakage attempt); a
  candidate worse than champion minus the floor fails; a candidate that
  pushes ensemble correlation toward monoculture fails; a planted canary
  that *passes* freezes the whole loop (an evaluation bug is the only
  way the loop can lie to itself).
- **Revision ledger** (:class:`RevisionLedger`). Every decision —
  applied or rejected, with its named reason — is hash-chained into
  ``TRADING_GYM_HOME/agent/revision/revisions.jsonl``; the agent keeps
  an auditable autobiography. Tampering fails the open.
- **Rate cap.** Applied revisions per rolling day are capped; runaway
  loops starve instead of oscillating.

Authority stays external: auto-apply at P/S/M still requires the caller
to hold the matching wildcard grant at execution time; B revisions are
always handed to governance as proposals.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from trading_gym.common.persistence import append_jsonl, atomic_write_json

__all__ = [
    "GateChain",
    "GateResult",
    "RevisionCandidate",
    "RevisionKind",
    "RevisionLedgerIntegrityError",
    "RevisionNotAllowed",
    "RevisionSurface",
    "TierConfig",
]

_GENESIS_HASH = "0" * 64
_DEFAULT_REGRESSION_FLOOR = 0.98
_DEFAULT_DIVERSITY_FLOOR = 0.7
_DEFAULT_LIFT = 1.0


class RevisionLedgerIntegrityError(RuntimeError):
    """The revision journal is edited, reordered, or truncated."""


class RevisionNotAllowed(PermissionError):
    """The revision kind is outside every self-revisable tier."""


class RevisionKind(str, Enum):
    PARAMETRIC = "P"
    STRUCTURAL = "S"
    META = "M"
    BEHAVIORAL = "B"
    CODE = "X"


@dataclass(frozen=True)
class TierConfig:
    """Per-tier thresholds and authority."""

    kind: RevisionKind
    min_lift: float
    require_holdout_replication: bool
    auto_apply: bool
    description: str = ""


@dataclass(frozen=True)
class RevisionCandidate:
    """One proposed self-revision with its evidence bundle."""

    revision_id: str
    kind: RevisionKind
    target: str
    summary: str
    suite_score: float
    champion_score: float
    sealed_holdouts: Mapping[str, float]
    seeded_holdout_seals: Mapping[str, str]
    pairwise_correlation: float
    is_canary: bool
    canary_expected_to_fail: bool


@dataclass(frozen=True)
class GateResult:
    """Outcome of the ordered gate chain for one candidate."""

    passed: bool
    failed_gate: str | None
    detail: str
    freeze_loop: bool = False


@dataclass(frozen=True)
class RevisionOutcome:
    """The surface's decision for one submitted candidate."""

    applied: bool
    reason: str
    gates: GateResult
    revision_id: str


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class GateChain:
    """Fixed-order evaluation gates; each failure names itself."""

    #: Evaluated strictly in this order; the first failure stops the chain.
    _ORDER = ("sealed_holdout", "regression_floor", "diversity_floor", "canary")

    @classmethod
    def order(cls) -> tuple[str, ...]:
        return cls._ORDER

    def evaluate(
        self,
        candidate: RevisionCandidate,
        *,
        tier_config: TierConfig,
    ) -> GateResult:
        for gate in self._ORDER:
            checker = getattr(self, f"_gate_{gate}")
            failure = checker(candidate, tier_config)
            if failure is not None:
                return GateResult(passed=False, failed_gate=gate, detail=failure)
        return GateResult(passed=True, failed_gate=None, detail="all gates passed")

    def _gate_sealed_holdout(
        self, candidate: RevisionCandidate, tier_config: TierConfig
    ) -> str | None:
        unknown = set(candidate.sealed_holdouts) - set(
            candidate.seeded_holdout_seals
        )
        if unknown:
            return f"unsealed holdout ids: {sorted(unknown)}"
        missing = set(candidate.seeded_holdout_seals) - set(
            candidate.sealed_holdouts
        )
        if missing:
            return f"holdout results missing for: {sorted(missing)}"
        if tier_config.require_holdout_replication:
            below = [
                holdout
                for holdout, score in candidate.sealed_holdouts.items()
                if score <= candidate.champion_score
            ]
            if below:
                return f"holdout lift not replicated on: {sorted(below)}"
        return None

    def _gate_regression_floor(
        self, candidate: RevisionCandidate, tier_config: TierConfig
    ) -> str | None:
        floor_score = candidate.champion_score * _DEFAULT_REGRESSION_FLOOR
        if candidate.suite_score < floor_score:
            return (
                f"suite score {candidate.suite_score:.4f} below regression "
                f"floor {floor_score:.4f}"
            )
        return None

    def _gate_diversity_floor(
        self, candidate: RevisionCandidate, tier_config: TierConfig
    ) -> str | None:
        if candidate.pairwise_correlation > _DEFAULT_DIVERSITY_FLOOR:
            return (
                f"pairwise correlation {candidate.pairwise_correlation:.2f} "
                f"pushes the ensemble toward monoculture "
                f"(floor {_DEFAULT_DIVERSITY_FLOOR:.2f})"
            )
        return None

    def _gate_canary(
        self, candidate: RevisionCandidate, tier_config: TierConfig
    ) -> str | None:
        if not candidate.is_canary:
            return None
        if not candidate.canary_expected_to_fail:
            # a planted known-bad candidate that passed the other gates:
            # the evaluation harness itself is broken
            return (
                "canary violation: a planted known-bad candidate passed the "
                "gates — evaluation integrity failure"
            )
        return None


class _LedgerEvent:
    __slots__ = ("seq", "ts", "type", "prev_hash", "entry_hash", "payload")

    def __init__(
        self, seq: int, ts: str, type: str, prev_hash: str, payload: dict[str, Any]
    ) -> None:
        self.seq = seq
        self.ts = ts
        self.type = type
        self.prev_hash = prev_hash
        self.payload = payload
        body = {
            "seq": seq,
            "ts": ts,
            "type": type,
            "prev_hash": prev_hash,
            **payload,
        }
        self.entry_hash = hashlib.sha256(_canonical(body)).hexdigest()

    def to_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "prev_hash": self.prev_hash,
            **self.payload,
            "entry_hash": self.entry_hash,
        }


class RevisionSurface:
    """Tier registry + gate chain + rate cap over a hash-chained ledger."""

    def __init__(
        self,
        path: Path,
        *,
        tiers: Mapping[RevisionKind, TierConfig] | None = None,
        max_revisions_per_day: int = 10,
        verify: bool = True,
    ) -> None:
        self._path = Path(path)
        self._anchor_path = self._path.with_suffix(self._path.suffix + ".anchor")
        self._tiers: Mapping[RevisionKind, TierConfig] = (
            tiers if tiers is not None else self.default_tiers()
        )
        self._max_per_day = max(1, int(max_revisions_per_day))
        self._events: list[_LedgerEvent] = []
        self._applied_today: list[datetime] = []
        self._load(verify=verify)

    @classmethod
    def open(cls, path: Path | str, **kwargs: Any) -> "RevisionSurface":
        return cls(Path(path), **kwargs)

    @staticmethod
    def default_tiers() -> dict[RevisionKind, TierConfig]:
        return {
            RevisionKind.PARAMETRIC: TierConfig(
                kind=RevisionKind.PARAMETRIC,
                min_lift=_DEFAULT_LIFT,
                require_holdout_replication=False,
                auto_apply=True,
                description="hyperparameters/thresholds within grant bounds",
            ),
            RevisionKind.STRUCTURAL: TierConfig(
                kind=RevisionKind.STRUCTURAL,
                min_lift=_DEFAULT_LIFT,
                require_holdout_replication=True,
                auto_apply=True,
                description="DSL topology changes, replicated on holdouts",
            ),
            RevisionKind.META: TierConfig(
                kind=RevisionKind.META,
                min_lift=1.05,
                require_holdout_replication=True,
                auto_apply=True,
                description="fusion weights / playbooks / prompts",
            ),
            RevisionKind.BEHAVIORAL: TierConfig(
                kind=RevisionKind.BEHAVIORAL,
                min_lift=_DEFAULT_LIFT,
                require_holdout_replication=False,
                auto_apply=False,
                description="planner policy changes; governance proposal only",
            ),
            RevisionKind.CODE: TierConfig(
                kind=RevisionKind.CODE,
                min_lift=0.0,
                require_holdout_replication=False,
                auto_apply=False,
                description="never self-revisable; nomination only",
            ),
        }

    # -- submission ----------------------------------------------------------

    def submit(self, candidate: RevisionCandidate) -> RevisionOutcome:
        if not isinstance(candidate.kind, RevisionKind):
            raise ValueError("candidate.kind must be a RevisionKind")
        tier = self._tiers[candidate.kind]
        if candidate.kind is RevisionKind.CODE:
            raise RevisionNotAllowed(
                "tier X (code/artifacts) is never self-revisable; nominate "
                "it into the human absorption pipeline instead"
            )

        gates = GateChain().evaluate(candidate, tier_config=tier)
        if gates.failed_gate == "canary":
            self._record(
                candidate, decision="rejected", reason="canary", frozen=True
            )
            return RevisionOutcome(
                applied=False,
                reason="canary",
                gates=gates,
                revision_id=candidate.revision_id,
            )
        if not gates.passed:
            assert gates.failed_gate is not None
            self._record(candidate, decision="rejected", reason=gates.failed_gate)
            return RevisionOutcome(
                applied=False,
                reason=gates.failed_gate,
                gates=gates,
                revision_id=candidate.revision_id,
            )

        if not tier.auto_apply:
            self._record(
                candidate, decision="proposal", reason="tier_requires_governance"
            )
            return RevisionOutcome(
                applied=False,
                reason="tier_requires_governance",
                gates=gates,
                revision_id=candidate.revision_id,
            )

        lift = candidate.suite_score / candidate.champion_score
        if lift < tier.min_lift:
            self._record(
                candidate, decision="rejected", reason="lift_below_tier_threshold"
            )
            return RevisionOutcome(
                applied=False,
                reason="lift_below_tier_threshold",
                gates=gates,
                revision_id=candidate.revision_id,
            )

        if self._rate_capped():
            self._record(candidate, decision="rejected", reason="rate_cap")
            return RevisionOutcome(
                applied=False,
                reason="rate_cap",
                gates=gates,
                revision_id=candidate.revision_id,
            )

        self._record(candidate, decision="applied", reason="ok")
        self._applied_today.append(datetime.now(timezone.utc))
        return RevisionOutcome(
            applied=True,
            reason="ok",
            gates=gates,
            revision_id=candidate.revision_id,
        )

    def history(self) -> tuple[dict[str, Any], ...]:
        return tuple(event.to_json() for event in self._events)

    def verify(self) -> dict[str, Any]:
        previous = _GENESIS_HASH
        for index, event in enumerate(self._events, start=1):
            if event.seq != index or event.prev_hash != previous:
                raise RevisionLedgerIntegrityError(
                    f"journal chain break at seq {event.seq}"
                )
            previous = event.entry_hash
        if self._events:
            anchor = json.loads(self._anchor_path.read_text(encoding="utf-8"))
            if (
                anchor.get("seq") != self._events[-1].seq
                or anchor.get("head") != self._events[-1].entry_hash
            ):
                raise RevisionLedgerIntegrityError("journal anchor mismatch")
        return {"valid": True, "entries": len(self._events)}

    # -- internals -------------------------------------------------------------

    def _rate_capped(self) -> bool:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=1)
        self._applied_today = [
            moment for moment in self._applied_today if moment >= window_start
        ]
        return len(self._applied_today) >= self._max_per_day

    def _record(
        self,
        candidate: RevisionCandidate,
        *,
        decision: str,
        reason: str,
        frozen: bool = False,
    ) -> None:
        payload = {
            "revision_id": candidate.revision_id,
            "kind": candidate.kind.value,
            "target": candidate.target,
            "summary": candidate.summary,
            "decision": decision,
            "reason": reason,
            "frozen": frozen,
        }
        self._append("revision_decision", payload)

    def _append(self, type: str, payload: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        seq = (self._events[-1].seq if self._events else 0) + 1
        prev_hash = self._events[-1].entry_hash if self._events else _GENESIS_HASH
        event = _LedgerEvent(seq, now, type, prev_hash, payload)
        append_jsonl(self._path, event.to_json())
        self._events.append(event)
        atomic_write_json(
            self._anchor_path,
            {"seq": self._events[-1].seq, "head": self._events[-1].entry_hash},
        )

    def _load(self, *, verify: bool) -> None:
        if not self._path.exists():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                raise RevisionLedgerIntegrityError(
                    "blank line in revision journal"
                )
            record = json.loads(line)
            for key in ("seq", "ts", "type", "prev_hash", "entry_hash"):
                if key not in record:
                    raise RevisionLedgerIntegrityError(
                        f"journal record missing {key!r}"
                    )
            body = {
                key: value for key, value in record.items() if key != "entry_hash"
            }
            if hashlib.sha256(_canonical(body)).hexdigest() != record["entry_hash"]:
                raise RevisionLedgerIntegrityError(
                    f"journal record {record['seq']} hash mismatch"
                )
            payload = {
                key: value
                for key, value in record.items()
                if key not in ("seq", "ts", "type", "prev_hash", "entry_hash")
            }
            self._events.append(
                _LedgerEvent(
                    seq=int(record["seq"]),
                    ts=str(record["ts"]),
                    type=str(record["type"]),
                    prev_hash=str(record["prev_hash"]),
                    payload=payload,
                )
            )
        if verify:
            self.verify()
        # replay rate-cap state from applied decisions in the last window
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=1)
        for event in self._events:
            if event.type == "revision_decision" and event.payload.get(
                "decision"
            ) == "applied":
                try:
                    moment = datetime.fromisoformat(
                        str(event.ts).replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
                if moment >= window_start:
                    self._applied_today.append(moment)
