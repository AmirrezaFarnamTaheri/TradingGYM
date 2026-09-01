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

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "context_tags": list(self.context_tags),
            "outcome": self.outcome,
            "lesson": self.lesson,
            "created_at": self.created_at,
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
        material = json.dumps(
            {"tags": list(tags), "outcome": outcome, "lesson": lesson,
             "t": created_at if created_at is not None else _now()},
            sort_keys=True,
        ).encode()
        memory_id = hashlib.sha256(material).hexdigest()[:16]
        entry = Experience(
            memory_id=memory_id,
            context_tags=tags,
            outcome=outcome,
            lesson=lesson[:400],
            created_at=float(created_at) if created_at is not None else _now(),
        )
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            del self._entries[0 : len(self._entries) - self.max_entries]
        return entry

    def query(self, tags: list[str], *, k: int = 3, now: float | None = None) -> list[Experience]:
        """Top-k experiences by tag overlap with recency-decay tiebreak."""
        wanted = {t.strip().lower() for t in tags if t.strip()}
        if not wanted or k <= 0:
            return []
        now_v = now if now is not None else _now()
        scored: list[tuple[float, Experience]] = []
        for e in self._entries:
            overlap = len(wanted & set(e.context_tags))
            if overlap == 0:
                continue
            age = max(0.0, now_v - e.created_at)
            recency = 0.5 ** (age / self.recency_half_life_seconds)
            scored.append((overlap * 10.0 + recency, e))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].created_at))
        return [e for _, e in scored[:k]]

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
                )
            )
        return mem

    def __len__(self) -> int:
        return len(self._entries)
