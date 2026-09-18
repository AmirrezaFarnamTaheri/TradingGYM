"""Auditable experience memory for agent workflows.

Absorbed mechanism: FenixAI's ReasoningBank (arXiv:2509.25140 reference in
`src/memory/reasoning_bank.py`) — agents record what happened, with tags and a
lesson, and later decisions retrieve the most relevant prior experiences.
Target-native design: tag-overlap + recency scoring, bounded size, JSON
export/import, no embeddings required (deterministic and dependency-free).

Advisory knowledge plane: memories inform proposals; they carry no authority.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class Experience:
    memory_id: str
    context_tags: tuple[str, ...]
    outcome: str  # "profitable" | "loss" | "neutral" | caller-defined
    lesson: str
    created_at: float
    #: How many times this exact lesson was recorded (consolidation, ADR-034).
    #: 1 for a single observation; the durable memory folds repeats.
    support: int = 1
    #: Timestamp of the most recent observation; ``created_at`` for support 1.
    last_seen_at: float | None = None

    @property
    def observed_at(self) -> float:
        """Most recent observation time (falls back to ``created_at``)."""

        return self.created_at if self.last_seen_at is None else self.last_seen_at

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "context_tags": list(self.context_tags),
            "outcome": self.outcome,
            "lesson": self.lesson,
            "created_at": self.created_at,
            "support": self.support,
            "last_seen_at": self.last_seen_at,
        }


@dataclass
class ExperienceMemory:
    max_entries: int = 500
    recency_half_life_seconds: float = 30 * 86400.0
    _entries: list[Experience] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        if self.recency_half_life_seconds <= 0:
            raise ValueError("recency_half_life_seconds must be positive")

    def record(
        self,
        *,
        context_tags: list[str],
        outcome: str,
        lesson: str,
        created_at: float | None = None,
    ) -> Experience:
        """Append one experience; oldest entries evicted past ``max_entries``."""
        tags = tuple(sorted({t.strip().lower() for t in context_tags if t.strip()}))
        if not tags:
            raise ValueError("at least one non-empty context tag is required")
        # One clock sample for both the id material and created_at. Two reads
        # of _now() made the id irreproducible from the record it identifies:
        # the stored timestamp and the hashed timestamp could differ by a
        # tick, so an entry rebuilt from to_dict() never recomputed its id.
        timestamp = float(created_at) if created_at is not None else _now()
        material = json.dumps(
            {"tags": list(tags), "outcome": outcome, "lesson": lesson,
             "t": timestamp},
            sort_keys=True,
        ).encode()
        memory_id = hashlib.sha256(material).hexdigest()[:16]
        entry = Experience(
            memory_id=memory_id,
            context_tags=tags,
            outcome=outcome,
            lesson=lesson[:400],
            created_at=timestamp,
        )
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            del self._entries[0 : len(self._entries) - self.max_entries]
        return entry

    def query(self, tags: list[str], *, k: int = 3, now: float | None = None) -> list[Experience]:
        """Top-k experiences by tag overlap, then recency and support.

        Overlap dominates (weight 10); the tiebreak adds recency decay on
        the *most recent* observation plus a bounded support bonus, so a
        lesson confirmed many times outranks a one-off of equal overlap
        and age. Both tiebreak terms stay below 1.0 — overlap ordering is
        never inverted by them.
        """
        wanted = {t.strip().lower() for t in tags if t.strip()}
        if not wanted or k <= 0:
            return []
        now_v = now if now is not None else _now()
        scored: list[tuple[float, Experience]] = []
        for e in self._entries:
            overlap = len(wanted & set(e.context_tags))
            if overlap == 0:
                continue
            age = max(0.0, now_v - e.observed_at)
            recency = 0.5 ** (age / self.recency_half_life_seconds)
            support_bonus = min(1.0, (max(1, e.support) - 1) / 4.0)
            scored.append((overlap * 10.0 + recency + support_bonus, e))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].observed_at))
        return [e for _, e in scored[:k]]

    def entries(self) -> tuple[Experience, ...]:
        """Current entries, oldest first."""

        return tuple(self._entries)

    def replace_entries(self, entries: list[Experience]) -> None:
        """Install a projection built elsewhere, keeping the size bound.

        The durable memory (ADR-034) derives a *consolidated* projection
        from its journal and installs it here; the newest entries are kept
        when the projection exceeds ``max_entries``.
        """

        if len(entries) > self.max_entries:
            entries = entries[len(entries) - self.max_entries :]
        self._entries = list(entries)

    def to_json(self) -> str:
        return json.dumps([e.to_dict() for e in self._entries], sort_keys=True)

    @classmethod
    def from_json(cls, payload: str, **kwargs) -> "ExperienceMemory":
        mem = cls(**kwargs)
        for row in json.loads(payload):
            mem._entries.append(
                Experience(
                    memory_id=row["memory_id"],
                    context_tags=tuple(row["context_tags"]),
                    outcome=row["outcome"],
                    lesson=row["lesson"],
                    created_at=float(row["created_at"]),
                    support=int(row.get("support", 1)),
                    last_seen_at=(
                        float(row["last_seen_at"])
                        if row.get("last_seen_at") is not None
                        else None
                    ),
                )
            )
        return mem

    def __len__(self) -> int:
        return len(self._entries)
