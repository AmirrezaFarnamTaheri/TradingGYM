from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import os
import tempfile
import threading

from filelock import FileLock

from trading_gym.common.persistence import resolve_data_home


@dataclass(frozen=True)
class EmergencyState:
    halted: bool = False
    revision: int = 0
    reason: str = ""
    actor: str = ""
    approver: str = ""


def apply_emergency(
    state: EmergencyState,
    *,
    halt: bool,
    reason: str,
    expected_revision: int,
    actor: str,
    approver: str,
) -> EmergencyState:
    if state.revision != expected_revision:
        raise RuntimeError("revision_conflict")
    actor = actor.strip()
    approver = approver.strip()
    if not actor or not approver:
        raise ValueError("actor_and_approver_required")
    if actor == approver:
        raise PermissionError("independent_approval_required")
    reason = reason.strip()
    if not reason:
        raise ValueError("reason_required")
    return EmergencyState(
        halted=bool(halt),
        revision=state.revision + 1,
        reason=reason,
        actor=actor,
        approver=approver,
    )


class EmergencyController:
    """Small durable CAS state machine shared by API and live execution."""

    def __init__(self, path: Path | None = None):
        self._explicit_path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._explicit_path or (resolve_data_home() / "production" / "emergency.json")

    def snapshot(self) -> EmergencyState:
        path = self.path
        if not path.exists():
            return EmergencyState()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return EmergencyState(**raw)

    def transition(
        self,
        *,
        halt: bool,
        reason: str,
        expected_revision: int,
        actor: str,
        approver: str,
    ) -> EmergencyState:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, FileLock(str(path) + ".lock", timeout=5):
            current = self.snapshot()
            next_state = apply_emergency(
                current,
                halt=halt,
                reason=reason,
                expected_revision=expected_revision,
                actor=actor,
                approver=approver,
            )
            payload = json.dumps(asdict(next_state), sort_keys=True, separators=(",", ":")) + "\n"
            fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, path)
            finally:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass
            return next_state
