"""Durable watchlists, notes, and dossier snapshots with CAS and audit recovery."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from trading_gym.core.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
)
from trading_gym.core.persistence import (
    atomic_write_json,
    read_json,
    resolve_data_home,
)
from trading_gym.core.security import bounded_json_dumps
from trading_gym.execution.governance.ledger import AuditLedger
from trading_gym.agent.research.models import ResearchDossier


class WorkspaceConflict(RuntimeError):
    pass


class WatchlistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: Annotated[str, Field(min_length=1, max_length=120)]
    tags: Annotated[list[str], Field(max_length=20)] = Field(default_factory=list)
    thesis: Annotated[str, Field(max_length=4000)] = ""
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")]
    subject: Annotated[str, Field(min_length=1, max_length=200)]
    body: Annotated[str, Field(max_length=50_000)]
    evidence_ids: Annotated[list[str], Field(max_length=500)] = Field(
        default_factory=list
    )
    revision: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime


_NOTE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")


class ResearchWorkspace:
    MAX_WATCHLIST = 10_000
    MAX_NOTES = 50_000
    MAX_REPORTS = 10_000

    def __init__(
        self,
        path: Path | None = None,
        ledger: AuditLedger | None = None,
    ) -> None:
        self.path = path or resolve_data_home() / "research-workspace.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".workspace.lock")

    @staticmethod
    def _validate_note_id(note_id: str) -> str:
        if not _NOTE_ID.fullmatch(note_id):
            raise ValueError(
                "note_id must be 1-120 characters using letters, digits, dot, underscore, or hyphen"
            )
        return note_id

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {
                "schema_version": 1,
                "revision": 0,
                "watchlist": {},
                "notes": {},
                "reports": {},
                "audit_outbox": {},
            },
        )
        if document.get("schema_version") != 1:
            raise ValueError("unsupported research workspace schema")
        limits = {
            "watchlist": self.MAX_WATCHLIST,
            "notes": self.MAX_NOTES,
            "reports": self.MAX_REPORTS,
        }
        for field, limit in limits.items():
            values = document.get(field)
            if not isinstance(values, dict):
                raise ValueError(f"research workspace {field} must be an object")
            if len(values) > limit:
                raise ValueError(f"research workspace {field} limit exceeded")
        for instrument_id, raw in document["watchlist"].items():
            entry = WatchlistEntry.model_validate(raw)
            if entry.instrument_id != instrument_id:
                raise ValueError("watchlist key does not match instrument_id")
        for note_id, raw in document["notes"].items():
            self._validate_note_id(note_id)
            note = ResearchNote.model_validate(raw)
            if note.note_id != note_id:
                raise ValueError("note key does not match note_id")
        for report_id, raw in document["reports"].items():
            if not re.fullmatch(r"[0-9a-f]{64}", report_id):
                raise ValueError("invalid report identifier")
            if not isinstance(raw, dict) or raw.get("report_id") != report_id:
                raise ValueError("report key does not match report_id")
        ensure_audit_outbox(document)
        return document

    def audit_pending(self) -> int:
        with self.lock:
            return len(self._load()["audit_outbox"])

    def reconcile_audit(self) -> dict[str, int]:
        with self.lock:
            document = self._load()
            delivered = deliver_audit_events(document, self.ledger)
            if delivered:
                atomic_write_json(self.path, document)
            return {"delivered": delivered, "pending": len(document["audit_outbox"])}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            document = self._load()
        return {
            "revision": document["revision"],
            "watchlist": list(document["watchlist"].values()),
            "notes": list(document["notes"].values()),
            "reports": list(document["reports"].values()),
        }

    def save_watchlist(
        self,
        entry: WatchlistEntry,
        *,
        expected_revision: int | None = None,
        actor: str = "api-operator",
    ) -> dict[str, Any]:
        bounded_json_dumps(entry.model_dump(mode="json"), max_bytes=64 * 1024)
        with self.lock:
            document = self._load()
            current = int(document["revision"])
            if expected_revision is not None and expected_revision != current:
                raise WorkspaceConflict(
                    f"expected workspace revision {expected_revision}, found {current}"
                )
            is_new = entry.instrument_id not in document["watchlist"]
            if is_new and len(document["watchlist"]) >= self.MAX_WATCHLIST:
                raise WorkspaceConflict("watchlist limit reached")
            document["watchlist"][entry.instrument_id] = entry.model_dump(mode="json")
            document["revision"] = current + 1
            queue_audit_event(
                document,
                event_id=(
                    f"research-watchlist:{entry.instrument_id}:workspace-revision:{document['revision']}:saved"
                ),
                event="research.watchlist_saved",
                actor=actor,
                subject=entry.instrument_id,
                details={
                    "workspace_revision": document["revision"],
                    "tags": entry.tags,
                },
            )
            atomic_write_json(self.path, document)
            result = {
                "revision": document["revision"],
                "entry": entry.model_dump(mode="json"),
            }
        self.reconcile_audit()
        return result

    def remove_watchlist(
        self,
        instrument_id: str,
        *,
        expected_revision: int,
        actor: str = "api-operator",
    ) -> int:
        with self.lock:
            document = self._load()
            current = int(document["revision"])
            if expected_revision != current:
                raise WorkspaceConflict(
                    f"expected workspace revision {expected_revision}, found {current}"
                )
            if document["watchlist"].pop(instrument_id, None) is None:
                raise KeyError(instrument_id)
            document["revision"] = current + 1
            queue_audit_event(
                document,
                event_id=(
                    f"research-watchlist:{instrument_id}:workspace-revision:{document['revision']}:removed"
                ),
                event="research.watchlist_removed",
                actor=actor,
                subject=instrument_id,
                details={"workspace_revision": document["revision"]},
            )
            atomic_write_json(self.path, document)
            revision = int(document["revision"])
        self.reconcile_audit()
        return revision

    def save_note(
        self,
        *,
        subject: str,
        body: str,
        evidence_ids: list[str] | None = None,
        note_id: str | None = None,
        expected_revision: int | None = None,
        actor: str = "api-operator",
    ) -> ResearchNote:
        now = datetime.now(timezone.utc)
        with self.lock:
            document = self._load()
            identifier = self._validate_note_id(note_id or uuid4().hex)
            existing = document["notes"].get(identifier)
            current_revision = int(existing["revision"]) if existing else 0
            if expected_revision is not None and expected_revision != current_revision:
                raise WorkspaceConflict(
                    f"expected note revision {expected_revision}, found {current_revision}"
                )
            if existing is None and len(document["notes"]) >= self.MAX_NOTES:
                raise WorkspaceConflict("research note limit reached")
            note = ResearchNote(
                note_id=identifier,
                subject=subject,
                body=body,
                evidence_ids=evidence_ids or [],
                revision=current_revision + 1,
                created_at=(
                    ResearchNote.model_validate(existing).created_at
                    if existing
                    else now
                ),
                updated_at=now,
            )
            bounded_json_dumps(note.model_dump(mode="json"), max_bytes=128 * 1024)
            document["notes"][identifier] = note.model_dump(mode="json")
            document["revision"] = int(document["revision"]) + 1
            queue_audit_event(
                document,
                event_id=f"research-note:{identifier}:revision:{note.revision}:saved",
                event="research.note_saved",
                actor=actor,
                subject=identifier,
                details={
                    "note_revision": note.revision,
                    "workspace_revision": document["revision"],
                    "evidence_count": len(note.evidence_ids),
                },
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return note

    def save_report(
        self,
        dossier: ResearchDossier,
        *,
        label: str = "",
        actor: str = "api-operator",
    ) -> dict[str, Any]:
        payload = dossier.model_dump_json()
        report_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self.lock:
            document = self._load()
            existing = document["reports"].get(report_id)
            normalised_label = label[:200]
            if existing is not None and existing.get("label") == normalised_label:
                result = {"revision": document["revision"], **existing}
            else:
                if existing is None and len(document["reports"]) >= self.MAX_REPORTS:
                    raise WorkspaceConflict("research report limit reached")
                record = {
                    "report_id": report_id,
                    "subject": dossier.subject,
                    "label": normalised_label,
                    "as_of": dossier.as_of.isoformat(),
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "dossier": dossier.model_dump(mode="json"),
                }
                bounded_json_dumps(record, max_bytes=512 * 1024)
                document["reports"][report_id] = record
                document["revision"] = int(document["revision"]) + 1
                queue_audit_event(
                    document,
                    event_id=(
                        f"research-report:{report_id}:workspace-revision:{document['revision']}:saved"
                    ),
                    event="research.report_saved",
                    actor=actor,
                    subject=report_id,
                    details={
                        "subject": dossier.subject,
                        "label": normalised_label,
                        "workspace_revision": document["revision"],
                    },
                )
                atomic_write_json(self.path, document)
                result = {"revision": document["revision"], **record}
        self.reconcile_audit()
        return result
