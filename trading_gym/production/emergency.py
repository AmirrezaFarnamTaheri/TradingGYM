from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import os
import tempfile
import threading

from filelock import FileLock

from trading_gym.common.persistence import resolve_data_home, _fsync_directory


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
        return self._explicit_path or (
            resolve_data_home() / "production" / "emergency.json"
        )

    def snapshot(self) -> EmergencyState:
        """Read the typed durable halt marker or reject malformed state."""
        path = self.path
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("invalid_emergency_state: symlink-backed state")
        if not path.exists():
            # A never-initialized installation starts unhalted. Malformed
            # *existing* state must never be interpreted as unhalted.
            return EmergencyState()
        try:
            with path.open("rb") as handle:
                payload = handle.read(64 * 1024 + 1)
            if len(payload) > 64 * 1024:
                raise ValueError("invalid_emergency_state: oversized record")
            raw = json.loads(payload)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_emergency_state: unreadable record") from exc
        if (
            not isinstance(raw, dict)
            or type(raw.get("halted")) is not bool
            or type(raw.get("revision")) is not int
            or raw["revision"] < 0
            or any(
                field in raw and not isinstance(raw[field], str)
                for field in ("reason", "actor", "approver")
            )
        ):
            raise ValueError("invalid_emergency_state: malformed fields")
        try:
            return EmergencyState(**raw)
        except TypeError as exc:
            raise ValueError("invalid_emergency_state: unexpected fields") from exc

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
            payload = (
                json.dumps(asdict(next_state), sort_keys=True, separators=(",", ":"))
                + "\n"
            )
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
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
            _fsync_directory(path.parent)
            return next_state
