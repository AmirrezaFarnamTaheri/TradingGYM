"""Durable market-data catalogue with provenance, quality, quarantine, and audit state."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_gym.common.audit_outbox import (
    deliver_audit_events,
    ensure_audit_outbox,
    queue_audit_event,
)
from trading_gym.common.persistence import atomic_write_json, read_json, resolve_data_home
from trading_gym.common.security import bounded_json_dumps
from trading_gym.governance.ledger import AuditLedger


class DatasetState(str, Enum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    SUPERSEDED = "superseded"


class DatasetRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: Annotated[str, Field(min_length=3, max_length=120)]
    timeframe: Annotated[str, Field(pattern=r"^(1m|3m|5m|15m|30m|1h|4h|1d|1w)$")]
    start: datetime
    end: datetime
    provider_id: Annotated[str, Field(min_length=1, max_length=80)]
    row_count: Annotated[int, Field(ge=1, le=2_000_000_000)]
    expected_rows: Annotated[int | None, Field(ge=1, le=2_000_000_000)] = None
    completeness: Annotated[float, Field(ge=0.0, le=1.0)]
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    schema_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    storage_ref: Annotated[str, Field(min_length=1, max_length=500)]
    warnings: Annotated[list[str], Field(max_length=100)] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    supersedes_dataset_id: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None

    @field_validator("storage_ref")
    @classmethod
    def validate_storage_ref(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("storage_ref contains control characters")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> "DatasetRegistration":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("dataset timestamps must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("dataset end must be after start")
        if self.expected_rows is not None:
            observed_ratio = min(1.0, self.row_count / self.expected_rows)
            if abs(observed_ratio - self.completeness) > 0.02:
                raise ValueError("completeness is inconsistent with row_count/expected_rows")
        bounded_json_dumps(self.metadata, max_bytes=64 * 1024)
        return self


class DatasetRecord(DatasetRegistration):
    model_config = ConfigDict(extra="forbid", frozen=False)

    dataset_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    state: DatasetState = DatasetState.ACTIVE
    revision: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime
    quarantine_reason: str | None = Field(default=None, max_length=2000)


class DatasetConflict(RuntimeError):
    pass


class DatasetCatalog:
    MAX_DATASETS = 100_000

    def __init__(
        self,
        path: Path | None = None,
        ledger: AuditLedger | None = None,
    ) -> None:
        self.path = path or resolve_data_home() / "market-data-catalog.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".catalog.lock")

    def _load(self) -> dict[str, Any]:
        document = read_json(
            self.path,
            {
                "schema_version": 1,
                "revision": 0,
                "datasets": {},
                "by_content": {},
                "audit_outbox": {},
            },
        )
        if document.get("schema_version") != 1:
            raise ValueError("unsupported dataset catalogue schema")
        for field in ("datasets", "by_content"):
            if not isinstance(document.get(field), dict):
                raise ValueError(f"dataset catalogue {field} must be an object")
        if len(document["datasets"]) > self.MAX_DATASETS:
            raise ValueError("dataset catalogue record limit exceeded")
        # The content index is derived but persisted for deterministic deduplication.
        for content_hash, dataset_id in document["by_content"].items():
            raw = document["datasets"].get(dataset_id)
            if raw is None or raw.get("content_sha256") != content_hash:
                raise ValueError("dataset catalogue content index is inconsistent")
        ensure_audit_outbox(document)
        return document

    @staticmethod
    def _dataset_id(request: DatasetRegistration) -> str:
        identity = "\0".join(
            [
                request.instrument_id,
                request.timeframe,
                request.start.astimezone(timezone.utc).isoformat(),
                request.end.astimezone(timezone.utc).isoformat(),
                request.provider_id,
                request.content_sha256,
            ]
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

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

    def revision(self) -> int:
        with self.lock:
            return int(self._load()["revision"])

    def list(
        self,
        *,
        instrument_id: str | None = None,
        timeframe: str | None = None,
        state: DatasetState | None = None,
    ) -> list[DatasetRecord]:
        with self.lock:
            document = self._load()
        records = [
            DatasetRecord.model_validate(item)
            for item in document["datasets"].values()
        ]
        if instrument_id is not None:
            records = [item for item in records if item.instrument_id == instrument_id]
        if timeframe is not None:
            records = [item for item in records if item.timeframe == timeframe]
        if state is not None:
            records = [item for item in records if item.state is state]
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def get(self, dataset_id: str) -> DatasetRecord:
        with self.lock:
            raw = self._load()["datasets"].get(dataset_id)
        if raw is None:
            raise KeyError(dataset_id)
        return DatasetRecord.model_validate(raw)

    def register(
        self,
        request: DatasetRegistration,
        *,
        expected_catalog_revision: int | None = None,
        actor: str = "api-operator",
    ) -> dict[str, Any]:
        mutated = False
        with self.lock:
            document = self._load()
            catalog_revision = int(document["revision"])
            if (
                expected_catalog_revision is not None
                and expected_catalog_revision != catalog_revision
            ):
                raise DatasetConflict(
                    f"expected catalog revision {expected_catalog_revision}, found {catalog_revision}"
                )
            existing_id = document["by_content"].get(request.content_sha256)
            if existing_id is not None:
                existing = DatasetRecord.model_validate(document["datasets"][existing_id])
                if (
                    existing.instrument_id != request.instrument_id
                    or existing.timeframe != request.timeframe
                ):
                    raise DatasetConflict(
                        "content hash is already registered under a different instrument/timeframe"
                    )
                result = {
                    "catalog_revision": catalog_revision,
                    "dataset": existing.model_dump(mode="json"),
                }
            else:
                dataset_id = self._dataset_id(request)
                if dataset_id in document["datasets"]:
                    result = {
                        "catalog_revision": catalog_revision,
                        "dataset": document["datasets"][dataset_id],
                    }
                else:
                    if len(document["datasets"]) >= self.MAX_DATASETS:
                        raise DatasetConflict("dataset catalogue record limit reached")
                    now = datetime.now(timezone.utc)
                    record = DatasetRecord(
                        **request.model_dump(),
                        dataset_id=dataset_id,
                        created_at=now,
                        updated_at=now,
                    )
                    next_catalog_revision = catalog_revision + 1
                    if request.supersedes_dataset_id is not None:
                        raw_previous = document["datasets"].get(request.supersedes_dataset_id)
                        if raw_previous is None:
                            raise KeyError(request.supersedes_dataset_id)
                        previous = DatasetRecord.model_validate(raw_previous)
                        if (
                            previous.instrument_id != request.instrument_id
                            or previous.timeframe != request.timeframe
                        ):
                            raise DatasetConflict(
                                "superseded dataset must have matching instrument/timeframe"
                            )
                        if previous.state is DatasetState.SUPERSEDED:
                            raise DatasetConflict("dataset is already superseded")
                        previous.state = DatasetState.SUPERSEDED
                        previous.revision += 1
                        previous.updated_at = now
                        document["datasets"][previous.dataset_id] = previous.model_dump(mode="json")
                        queue_audit_event(
                            document,
                            event_id=(
                                f"dataset:{previous.dataset_id}:revision:{previous.revision}:superseded"
                            ),
                            event="dataset.superseded",
                            actor=actor,
                            subject=previous.dataset_id,
                            details={
                                "replacement_dataset_id": dataset_id,
                                "catalog_revision": next_catalog_revision,
                            },
                        )
                    document["datasets"][dataset_id] = record.model_dump(mode="json")
                    document["by_content"][request.content_sha256] = dataset_id
                    document["revision"] = next_catalog_revision
                    queue_audit_event(
                        document,
                        event_id=f"dataset:{dataset_id}:revision:1:registered",
                        event="dataset.registered",
                        actor=actor,
                        subject=dataset_id,
                        details={
                            "instrument_id": request.instrument_id,
                            "timeframe": request.timeframe,
                            "provider_id": request.provider_id,
                            "content_sha256": request.content_sha256,
                            "catalog_revision": next_catalog_revision,
                        },
                    )
                    atomic_write_json(self.path, document)
                    mutated = True
                    result = {
                        "catalog_revision": document["revision"],
                        "dataset": record.model_dump(mode="json"),
                    }
        # Existing-state retries also flush an earlier committed audit intent.
        self.reconcile_audit()
        return result

    def quarantine(
        self,
        dataset_id: str,
        *,
        expected_revision: int,
        reason: str,
        actor: str = "api-operator",
    ) -> DatasetRecord:
        with self.lock:
            document = self._load()
            raw = document["datasets"].get(dataset_id)
            if raw is None:
                raise KeyError(dataset_id)
            record = DatasetRecord.model_validate(raw)
            if record.revision != expected_revision:
                raise DatasetConflict(
                    f"expected dataset revision {expected_revision}, found {record.revision}"
                )
            if record.state is DatasetState.SUPERSEDED:
                raise DatasetConflict("superseded dataset cannot be quarantined")
            if record.state is DatasetState.QUARANTINED:
                result = record
            else:
                record.state = DatasetState.QUARANTINED
                record.quarantine_reason = reason[:2000] or "quarantined by operator"
                record.revision += 1
                record.updated_at = datetime.now(timezone.utc)
                document["datasets"][dataset_id] = record.model_dump(mode="json")
                document["revision"] = int(document["revision"]) + 1
                queue_audit_event(
                    document,
                    event_id=f"dataset:{dataset_id}:revision:{record.revision}:quarantined",
                    event="dataset.quarantined",
                    actor=actor,
                    subject=dataset_id,
                    details={
                        "reason": record.quarantine_reason,
                        "catalog_revision": document["revision"],
                    },
                )
                atomic_write_json(self.path, document)
                result = record
        self.reconcile_audit()
        return result
