"""AXIOM-1 long-term memory: consolidating, tamper-evident experience journal (ADR-034).

Wraps the deterministic tag-overlap + recency scoring of
:class:`~trading_gym.agent.experience_memory.ExperienceMemory` with the
platform's durability guarantees, and adds the two properties a
long-term memory needs to stay useful rather than merely large:

**Append-only journal, consolidated projection.** Every ``record`` is
appended to ``TRADING_GYM_HOME/agent/memory/experiences.jsonl`` through
the crash-safe helper and carries its own integrity hash, so a laundered
lesson fails the open instead of silently poisoning future decisions.
The in-memory state is a *projection*: journal records with identical
(tags, outcome, lesson) fold into one :class:`Experience` whose
``support`` counts the observations and whose ``last_seen_at`` tracks
the newest. A lesson observed twenty times occupies one retrieval slot
and carries twenty times the evidential weight — the journal stays the
literal truth, the projection stays readable.

**Retention.** Entries older than ``retention_days`` are pruned at open
time and after each append, and the pruned state is written back, so
the journal cannot grow without bound and stale lessons cannot
resurface.

**Advice, not raw rows.** :meth:`advice` returns a bounded digest —
warnings drawn from losses, confirmations from wins, each with its
support count, plus a net signal in [-1, 1]. Callers (the forge, the
tool registry, an operator UI) consume the digest; nothing here carries
authority, and a memory that has seen nothing says so.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from trading_gym.agent.experience_memory import Experience, ExperienceMemory
from trading_gym.core.persistence import (
    append_jsonl,
    atomic_write_json,
    atomic_write_text,
)

__all__ = [
    "MemoryAdvice",
    "MemoryJournalIntegrityError",
    "PersistentExperienceMemory",
]

_GENESIS = "0" * 64

#: Outcomes treated as negative/positive evidence by :meth:`advice`.
_LOSS_OUTCOMES = frozenset({"loss", "losing", "negative", "failure", "failed"})
_WIN_OUTCOMES = frozenset({"profitable", "profit", "win", "positive", "success"})


class MemoryJournalIntegrityError(RuntimeError):
    """The experience journal is edited, reordered, or truncated."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


@dataclass(frozen=True)
class MemoryAdvice:
    """Bounded digest of what the memory knows about a tag set."""

    tags: tuple[str, ...]
    warnings: tuple[tuple[str, int], ...]
    confirmations: tuple[tuple[str, int], ...]
    net_signal: float
    consulted: int

    @property
    def empty(self) -> bool:
        return self.consulted == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tags": list(self.tags),
            "warnings": [
                {"lesson": lesson, "support": support}
                for lesson, support in self.warnings
            ],
            "confirmations": [
                {"lesson": lesson, "support": support}
                for lesson, support in self.confirmations
            ],
            "net_signal": self.net_signal,
            "consulted": self.consulted,
        }


def memory_advice(tags: Sequence[str], hits: Sequence[Experience]) -> MemoryAdvice:
    """One advisory digest implementation for durable and derived memories."""
    warnings, confirmations = [], []
    loss_support = win_support = 0
    for hit in hits:
        # Legacy forge journals used "profitable" for an applied revision.
        # Retain those rows as process history, but do not count them as cash
        # outcomes. Actual financial memory is projected from ledger evidence.
        if any(tag.startswith("forge:") for tag in hit.context_tags):
            continue
        outcome = hit.outcome.strip().lower()
        if outcome in _LOSS_OUTCOMES:
            warnings.append((hit.lesson, hit.support))
            loss_support += hit.support
        elif outcome in _WIN_OUTCOMES:
            confirmations.append((hit.lesson, hit.support))
            win_support += hit.support
    total = loss_support + win_support
    return MemoryAdvice(
        tags=tuple(_normalise_tags(list(tags))),
        warnings=tuple(warnings),
        confirmations=tuple(confirmations),
        net_signal=0.0 if total == 0 else (win_support - loss_support) / total,
        consulted=len(hits),
    )


class PersistentExperienceMemory:
    """ExperienceMemory with a crash-safe, integrity-checked journal."""

    def __init__(
        self,
        path: Path,
        *,
        max_entries: int = 500,
        recency_half_life_seconds: float = 30 * 86400.0,
        retention_days: float = 180.0,
        read_only: bool = False,
    ) -> None:
        if type(read_only) is not bool:  # noqa: E721
            raise ValueError("read_only must be a boolean")
        self._read_only = read_only
        self._memory = ExperienceMemory(
            max_entries=max_entries,
            recency_half_life_seconds=recency_half_life_seconds,
        )
        self._path = Path(path)
        self._anchor_path = self._path.with_suffix(self._path.suffix + ".anchor")
        self._retention_days = float(retention_days)
        self._journal: list[dict[str, Any]] = []
        self._load_and_prune()

    @classmethod
    def open(cls, path: Path | str, **kwargs: Any) -> "PersistentExperienceMemory":
        return cls(Path(path), **kwargs)

    # -- public API (mirrors ExperienceMemory) ---------------------------------

    def record(
        self,
        *,
        context_tags: list[str],
        outcome: str,
        lesson: str,
        created_at: float | None = None,
    ) -> Experience:
        """Append one observation; identical lessons consolidate on read."""

        if self._read_only:
            raise PermissionError("read-only memory cannot append records")
        timestamp = float(created_at) if created_at is not None else _now()
        if not math.isfinite(timestamp):
            raise ValueError("experience timestamp must be finite")
        tags = _normalise_tags(context_tags)
        bounded_lesson = lesson[:400]
        entry: dict[str, Any] = {
            "context_tags": list(tags),
            "outcome": outcome,
            "lesson": bounded_lesson,
            "created_at": timestamp,
            "recorded_at": _now(),
        }
        self._append_journal(entry)
        self._project()
        consolidated = self._by_material(tags, outcome, bounded_lesson)
        if consolidated is None:  # pragma: no cover - defensive
            raise MemoryJournalIntegrityError(
                "the record just appended is absent from the projection"
            )
        return consolidated

    def query(
        self, tags: list[str], *, k: int = 3, now: float | None = None
    ) -> list[Experience]:
        now = _now() if now is None else now
        if not math.isfinite(now):
            raise ValueError("memory as-of time must be finite")
        # Consolidation must happen AFTER the time filter. Filtering only the
        # latest consolidated timestamp either leaks future support or hides
        # an earlier valid observation of the same lesson.
        memory = ExperienceMemory(
            self._memory.max_entries, self._memory.recency_half_life_seconds
        )
        memory.replace_entries(self._projection(as_of=now))
        return memory.query(tags, k=k, now=now)

    def advice(
        self,
        tags: Sequence[str],
        *,
        k: int = 5,
        now: float | None = None,
    ) -> MemoryAdvice:
        """Bounded, support-weighted digest for a tag set.

        ``net_signal`` is ``(wins - losses) / (wins + losses)`` over the
        consulted entries' support counts — +1 means every relevant
        observation was a win, -1 every one a loss, 0.0 means balanced or
        no directional evidence. Advisory: it carries no authority.
        """

        return memory_advice(tags, self.query(list(tags), k=max(0, k), now=now))

    def entries(self) -> tuple[Experience, ...]:
        """Consolidated projection, oldest first."""

        return self._memory.entries()

    def observations(self) -> int:
        """Journal record count (pre-consolidation)."""

        return len(self._journal)

    def verify(self) -> dict[str, Any]:
        """Re-read the journal and confirm integrity plus the anchor."""

        replay = PersistentExperienceMemory(
            self._path,
            max_entries=self._memory.max_entries,
            recency_half_life_seconds=self._memory.recency_half_life_seconds,
            retention_days=self._retention_days,
            read_only=True,
        )
        return {
            "valid": True,
            "observations": replay.observations(),
            "entries": len(replay),
        }

    def __len__(self) -> int:
        return len(self._memory)

    # -- journal -----------------------------------------------------------------

    def _append_journal(self, record: dict[str, Any]) -> None:
        seq = len(self._journal) + 1
        payload = {"seq": seq, "record": record}
        record_hash = hashlib.sha256(_canonical(payload)).hexdigest()
        journal_entry = {**payload, "record_sha256": record_hash}
        append_jsonl(self._path, journal_entry)
        self._journal.append(journal_entry)
        atomic_write_json(self._anchor_path, {"seq": seq, "head": record_hash})

    def _load_and_prune(self) -> None:
        if not self._path.exists():
            if self._anchor_path.exists() or self._anchor_path.is_symlink():
                raise MemoryJournalIntegrityError(
                    "experience journal missing with an existing anchor"
                )
            return
        cutoff = _now() - self._retention_days * 86400.0
        all_entries: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        for position, line in enumerate(
            self._path.read_text(encoding="utf-8").splitlines()
        ):
            if not line.strip():
                raise MemoryJournalIntegrityError("blank line in experience journal")
            try:
                entry = json.loads(line)
            except (ValueError, TypeError) as exc:
                raise MemoryJournalIntegrityError(
                    f"malformed experience record at position {position}"
                ) from exc
            if not isinstance(entry, dict):
                raise MemoryJournalIntegrityError(
                    f"experience record at position {position} is not an object"
                )
            for key in ("seq", "record", "record_sha256"):
                if key not in entry:
                    raise MemoryJournalIntegrityError(f"journal record missing {key!r}")
            # Seq must be contiguous from 1: deleting an interior line leaves
            # the per-record hashes otherwise intact (each covers only its own
            # seq and record), so a hole in the journal went undetected.
            if entry["seq"] != position + 1:
                raise MemoryJournalIntegrityError(
                    f"experience journal seq broken at position {position}: "
                    f"found {entry['seq']}, expected {position + 1}"
                )
            computed = hashlib.sha256(
                _canonical({"seq": entry["seq"], "record": entry["record"]})
            ).hexdigest()
            if computed != entry["record_sha256"]:
                raise MemoryJournalIntegrityError(
                    f"experience journal record {entry['seq']} hash mismatch"
                )
            if entry["record_sha256"] in seen_hashes:
                raise MemoryJournalIntegrityError(
                    f"duplicate journal record seq {entry['seq']}"
                )
            seen_hashes.add(entry["record_sha256"])
            all_entries.append(entry)

        # The anchor pins the sealed tail of the journal as written, so it is
        # checked against the full record list before retention drops anything.
        self._verify_anchor(all_entries)

        kept = [
            entry
            for entry in all_entries
            if float(entry["record"]["created_at"]) >= cutoff
        ]
        # the pruned state is the durable state
        if len(kept) != len(all_entries) and not self._read_only:
            kept = self._rewrite(kept)
        self._journal = kept
        self._project()

    def _verify_anchor(self, entries: list[dict[str, Any]]) -> None:
        """Confirm the sealed tail still pins the journal's last record.

        A missing anchor is legal — a chain-only journal, or sealing not yet
        enabled. A present anchor whose seq or head disagrees with the last
        record proves the file was truncated or its tail replaced.
        """

        if not self._anchor_path.exists():
            return
        try:
            anchor = json.loads(self._anchor_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MemoryJournalIntegrityError(
                "experience journal anchor is unreadable"
            ) from exc
        if not isinstance(anchor, dict):
            raise MemoryJournalIntegrityError("experience journal anchor is malformed")
        seq = anchor.get("seq")
        head = anchor.get("head")
        if type(seq) is not int or not isinstance(head, str):  # noqa: E721
            raise MemoryJournalIntegrityError("experience journal anchor is malformed")
        expected_seq = entries[-1]["seq"] if entries else 0
        expected_head = entries[-1]["record_sha256"] if entries else _GENESIS
        if seq != expected_seq or head != expected_head:
            raise MemoryJournalIntegrityError(
                "experience journal anchor does not match the sealed tail "
                f"(anchor seq={seq}, journal ends at seq={expected_seq}); "
                "the journal was truncated after sealing"
            )

    def _project(self) -> None:
        self._memory.replace_entries(self._projection())

    def _projection(self, *, as_of: float | None = None) -> list[Experience]:
        """Rebuild the consolidated in-memory projection from the journal.

        Records with identical (tags, outcome, lesson) fold into one
        entry: ``created_at`` keeps the first observation, ``support``
        counts them, ``last_seen_at`` tracks the newest. Ordering follows
        first observation, so the projection is a deterministic function
        of the journal.
        """

        folded: dict[tuple[tuple[str, ...], str, str], Experience] = {}
        order: list[tuple[tuple[str, ...], str, str]] = []
        for entry in self._journal:
            record = entry["record"]
            if (
                as_of is not None
                and max(
                    float(record["created_at"]),
                    float(record.get("recorded_at", record["created_at"])),
                )
                > as_of
            ):
                continue
            tags = tuple(record["context_tags"])
            key = (tags, str(record["outcome"]), str(record["lesson"]))
            created = float(record["created_at"])
            existing = folded.get(key)
            if existing is None:
                order.append(key)
                folded[key] = Experience(
                    memory_id=_material_id(tags, key[1], key[2]),
                    context_tags=tags,
                    outcome=key[1],
                    lesson=key[2],
                    created_at=created,
                    support=1,
                    last_seen_at=created,
                )
                continue
            folded[key] = Experience(
                memory_id=existing.memory_id,
                context_tags=existing.context_tags,
                outcome=existing.outcome,
                lesson=existing.lesson,
                created_at=min(existing.created_at, created),
                support=existing.support + 1,
                last_seen_at=max(existing.observed_at, created),
            )
        projection = [folded[key] for key in order]
        return projection

    def _by_material(
        self, tags: tuple[str, ...], outcome: str, lesson: str
    ) -> Experience | None:
        target = _material_id(tags, outcome, lesson)
        for entry in reversed(self._memory.entries()):
            if entry.memory_id == target:
                return entry
        return None

    def _rewrite(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rewrite retention's survivors as a fresh, self-consistent journal.

        Pruning is the one legitimate rewrite, and the pruned state must be
        as verifiable as the original: seqs are renumbered 1..N and every
        record hash recomputed over its new seq, so the next open sees a
        contiguous chain whose anchor pins its own tail. Returns the
        rewritten entries so the in-memory journal matches the file.
        """

        rewritten: list[dict[str, Any]] = []
        for position, entry in enumerate(entries, start=1):
            payload = {"seq": position, "record": entry["record"]}
            rewritten.append(
                {
                    **payload,
                    "record_sha256": hashlib.sha256(_canonical(payload)).hexdigest(),
                }
            )
        lines = "".join(
            json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for entry in rewritten
        )
        atomic_write_text(self._path, lines, max_bytes=64 * 1024 * 1024)
        head = rewritten[-1]["record_sha256"] if rewritten else _GENESIS
        atomic_write_json(
            self._anchor_path,
            {"seq": len(rewritten), "head": head},
        )
        return rewritten


def _normalise_tags(context_tags: list[str]) -> tuple[str, ...]:
    tags = tuple(sorted({tag.strip().lower() for tag in context_tags if tag.strip()}))
    if not tags:
        raise ValueError("at least one non-empty context tag is required")
    return tags


def _material_id(tags: tuple[str, ...], outcome: str, lesson: str) -> str:
    material = _canonical({"tags": list(tags), "outcome": outcome, "lesson": lesson})
    return hashlib.sha256(material).hexdigest()[:16]
