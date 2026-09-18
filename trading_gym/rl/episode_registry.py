"""Event-conditioned episode registry for RL research.

Absorbed mechanism: trading-gym-development typed its dataset directories by
prior-day market events ("reached upper limit yesterday", "opens +5%") so
training episodes were sampled by market regime instead of uniformly
(`core/tgym.py:143-145`, README event taxonomy).  Target-native port as a
content-addressed registry: every episode's identity is derived from its data
provenance + bounds + event type, deduplicated by that key, and queryable by
event type for curriculum-style samplers.

Pure bookkeeping — no data loading, no authority, deterministic keys.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EpisodeSpec:
    key: str
    source_sha256: str
    start_index: int
    end_index: int
    event_type: str
    meta: dict = field(default_factory=dict)


def make_episode_spec(
    *,
    source_sha256: str,
    start_index: int,
    end_index: int,
    event_type: str,
    meta: dict | None = None,
) -> EpisodeSpec:
    """Build a content-addressed episode spec.

    ``source_sha256`` identifies the admitted corpus slice; ``event_type`` is
    a caller-defined market-event label (e.g. ``"gap_up_open"``);
    ``meta`` must be JSON-serialisable scalars only.
    """
    if not hashlib.sha256().hexdigest or not isinstance(source_sha256, str):
        raise TypeError("source_sha256 must be a string")
    if len(source_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in source_sha256.lower()
    ):
        raise ValueError("source_sha256 must be a 64-char lowercase hex digest")
    source_sha256 = source_sha256.lower()
    if not 0 <= start_index < end_index:
        raise ValueError("require 0 <= start_index < end_index")
    if not re_fullmatch_event_type(event_type):
        raise ValueError("event_type must match ^[a-z0-9_-]{1,60}$")
    canonical = {
        "source_sha256": source_sha256,
        "start_index": start_index,
        "end_index": end_index,
        "event_type": event_type,
        "meta": meta or {},
    }
    key = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return EpisodeSpec(
        key=key,
        source_sha256=source_sha256,
        start_index=start_index,
        end_index=end_index,
        event_type=event_type,
        meta=dict(meta or {}),
    )


def re_fullmatch_event_type(event_type: str) -> bool:
    import re

    return re.fullmatch(r"[a-z0-9_-]{1,60}", event_type or "") is not None


class DuplicateEpisodeError(RuntimeError):
    pass


class EpisodeRegistry:
    """Deduplicating registry keyed by content address."""

    def __init__(self) -> None:
        self._by_key: dict[str, EpisodeSpec] = {}

    def register(self, spec: EpisodeSpec) -> bool:
        """Register an episode; returns False when the exact spec existed."""
        if spec.key in self._by_key:
            return False
        self._by_key[spec.key] = spec
        return True

    def register_or_raise(self, spec: EpisodeSpec) -> None:
        if not self.register(spec):
            raise DuplicateEpisodeError(f"episode {spec.key[:12]} already registered")

    def get(self, key: str) -> EpisodeSpec | None:
        return self._by_key.get(key)

    def by_event_type(self, event_type: str) -> list[EpisodeSpec]:
        return sorted(
            (s for s in self._by_key.values() if s.event_type == event_type),
            key=lambda s: s.key,
        )

    def all(self) -> list[EpisodeSpec]:
        return sorted(self._by_key.values(), key=lambda s: s.key)

    def __len__(self) -> int:
        return len(self._by_key)
