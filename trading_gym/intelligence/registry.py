"""Backend registry with availability-based auto-selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.types import BackendUnavailable


@dataclass(frozen=True)
class SelectionEntry:
    backend: IntelligenceBackend
    priority: int  # lower = preferred


class BackendRegistry:
    """Ordered registry; ``select`` walks it until one backend can act."""

    def __init__(
        self, entries: Iterable[tuple[IntelligenceBackend, int]] | None = None
    ):
        self._entries: list[SelectionEntry] = []
        for backend, priority in entries or []:
            self.register(backend, priority=priority)

    def register(self, backend: IntelligenceBackend, *, priority: int) -> None:
        if any(entry.backend.name == backend.name for entry in self._entries):
            raise ValueError(f"backend {backend.name!r} already registered")
        self._entries.append(SelectionEntry(backend=backend, priority=int(priority)))
        self._entries.sort(key=lambda e: e.priority)

    def list_backends(self) -> list[tuple[str, int]]:
        return [(e.backend.name, e.priority) for e in self._entries]

    def select(
        self,
        name: str | None = None,
        *,
        require_available: bool = True,
    ) -> IntelligenceBackend:
        """Pick by exact name (availability-checked) or walk the auto order.

        ``name=None`` means auto: first *available* backend in priority order.
        Unknown names raise :class:`BackendUnavailable`; all-unavailable raises
        too — callers own their final fallback (the rules floor is usually
        registered last precisely so auto never exhausts).
        """
        if name is not None and name != "auto":
            for entry in self._entries:
                if entry.backend.name == name:
                    if require_available and not entry.backend.is_available():
                        raise BackendUnavailable(f"backend {name} is not available")
                    return entry.backend
            raise BackendUnavailable(f"unknown backend {name!r}")

        errors: list[str] = []
        for entry in self._entries:
            try:
                if entry.backend.is_available():
                    return entry.backend
                errors.append(f"{entry.backend.name}:unavailable")
            except Exception as exc:  # noqa: BLE001 - probe failures degrade
                errors.append(f"{entry.backend.name}:{type(exc).__name__}")
        raise BackendUnavailable(
            "no available intelligence backend (" + "; ".join(errors) + ")"
        )
