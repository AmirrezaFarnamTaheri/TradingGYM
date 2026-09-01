"""Evidence-only recovery-point and recovery-time objective evaluation.

The evaluator measures snapshot freshness, off-host coverage, and isolated
restore-drill duration for required durable stores.  It cannot create backups,
restore state, switch traffic, or declare a disaster.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.operations.resilience import RestoreDrillReport


_STORE = r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$"
_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,199}$"


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class RecoverySnapshotEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: Annotated[str, Field(pattern=_ID)]
    captured_at: datetime
    verified_at: datetime
    valid: bool
    off_host_copy: bool
    covered_stores: list[Annotated[str, Field(pattern=_STORE)]] = Field(min_length=1, max_length=1000)
    manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def validate_snapshot(self) -> "RecoverySnapshotEvidence":
        _aware(self.captured_at, "captured_at")
        _aware(self.verified_at, "verified_at")
        if self.verified_at < self.captured_at:
            raise ValueError("verified_at must not precede captured_at")
        if len(set(self.covered_stores)) != len(self.covered_stores):
            raise ValueError("covered_stores must be unique")
        return self


class RecoveryDrillEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completed_at: datetime
    covered_stores: list[Annotated[str, Field(pattern=_STORE)]] = Field(min_length=1, max_length=1000)
    report: RestoreDrillReport

    @model_validator(mode="after")
    def validate_drill(self) -> "RecoveryDrillEvidence":
        _aware(self.completed_at, "completed_at")
        if len(set(self.covered_stores)) != len(self.covered_stores):
            raise ValueError("covered_stores must be unique")
        return self


class RecoveryObjectivePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_stores: list[Annotated[str, Field(pattern=_STORE)]] = Field(min_length=1, max_length=1000)
    maximum_rpo_seconds: Annotated[int, Field(ge=1, le=31_536_000)]
    maximum_rto_seconds: Annotated[int, Field(ge=1, le=31_536_000)]
    maximum_drill_age_seconds: Annotated[int, Field(ge=1, le=31_536_000)] = 2_592_000
    require_off_host_copy: bool = True

    @model_validator(mode="after")
    def validate_policy(self) -> "RecoveryObjectivePolicy":
        if len(set(self.required_stores)) != len(self.required_stores):
            raise ValueError("required_stores must be unique")
        return self


class RecoveryObjectiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    snapshots: list[RecoverySnapshotEvidence] = Field(max_length=10_000)
    drills: list[RecoveryDrillEvidence] = Field(max_length=10_000)
    policy: RecoveryObjectivePolicy

    @model_validator(mode="after")
    def validate_request(self) -> "RecoveryObjectiveRequest":
        _aware(self.evaluated_at, "evaluated_at")
        return self


class StoreRecoveryStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    store: str
    latest_snapshot_id: str | None
    rpo_seconds: int | None = Field(default=None, ge=0)
    latest_drill_snapshot_id: str | None
    observed_rto_seconds: float | None = Field(default=None, ge=0)
    off_host_copy: bool
    ready: bool
    blockers: list[str]


class RecoveryObjectiveReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    stores: list[StoreRecoveryStatus]
    blockers: list[str]
    warnings: list[str]
    ready_for_human_review: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    backup_authority_granted: bool = False
    restore_authority_granted: bool = False
    failover_authority_granted: bool = False
    mutation_performed: bool = False


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def evaluate_recovery_objectives(request: RecoveryObjectiveRequest) -> RecoveryObjectiveReport:
    blockers: list[str] = []
    warnings: list[str] = []
    statuses: list[StoreRecoveryStatus] = []
    snapshot_ids = [item.snapshot_id for item in request.snapshots]
    if len(set(snapshot_ids)) != len(snapshot_ids):
        blockers.append("duplicate_snapshot_evidence")

    for store in sorted(request.policy.required_stores):
        store_blockers: list[str] = []
        snapshots = [
            item for item in request.snapshots
            if store in item.covered_stores and item.captured_at <= request.evaluated_at
        ]
        valid_snapshots = [item for item in snapshots if item.valid]
        latest = max(valid_snapshots, key=lambda item: item.captured_at, default=None)
        rpo_seconds: int | None = None
        if latest is None:
            store_blockers.append("valid_snapshot_missing")
        else:
            rpo_seconds = int((request.evaluated_at - latest.captured_at).total_seconds())
            if rpo_seconds > request.policy.maximum_rpo_seconds:
                store_blockers.append("rpo_exceeded")
            if request.policy.require_off_host_copy and not latest.off_host_copy:
                store_blockers.append("off_host_copy_missing")

        drills = [
            item for item in request.drills
            if store in item.covered_stores and item.completed_at <= request.evaluated_at
        ]
        valid_drills = [
            item for item in drills
            if item.report.valid
            and not item.report.source_mutation_performed
            and not item.report.restore_mutation_performed
        ]
        latest_drill = max(valid_drills, key=lambda item: item.completed_at, default=None)
        rto: float | None = None
        if latest_drill is None:
            store_blockers.append("valid_restore_drill_missing")
        else:
            drill_age = (request.evaluated_at - latest_drill.completed_at).total_seconds()
            if drill_age > request.policy.maximum_drill_age_seconds:
                store_blockers.append("restore_drill_stale")
            rto = latest_drill.report.duration_ms / 1000.0
            if rto > request.policy.maximum_rto_seconds:
                store_blockers.append("rto_exceeded")

        for marker in store_blockers:
            blockers.append(f"{marker}:{store}")
        statuses.append(StoreRecoveryStatus(
            store=store,
            latest_snapshot_id=latest.snapshot_id if latest else None,
            rpo_seconds=rpo_seconds,
            latest_drill_snapshot_id=latest_drill.report.snapshot_id if latest_drill else None,
            observed_rto_seconds=rto,
            off_host_copy=bool(latest and latest.off_host_copy),
            ready=not store_blockers,
            blockers=sorted(set(store_blockers)),
        ))

    blockers = sorted(set(blockers))
    payload = {
        "evaluated_at": request.evaluated_at.isoformat(),
        "stores": [item.model_dump(mode="json") for item in statuses],
        "blockers": blockers,
        "warnings": warnings,
    }
    return RecoveryObjectiveReport(
        evaluated_at=request.evaluated_at,
        stores=statuses,
        blockers=blockers,
        warnings=warnings,
        ready_for_human_review=not blockers,
        report_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )
