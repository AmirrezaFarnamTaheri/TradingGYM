"""Deterministic feature-drift baselines and bounded evaluation."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, pstdev
from typing import Annotated, Any, Literal
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_gym.common.audit_outbox import deliver_audit_events, ensure_audit_outbox, queue_audit_event
from trading_gym.common.persistence import atomic_write_json, read_json, resolve_data_home
from trading_gym.governance.ledger import AuditLedger


MAX_SAMPLES = 1_000_000
MAX_BASELINES = 10_000
EPSILON = 1e-9


class DriftPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    warning_psi: Annotated[float, Field(gt=0.0, le=100.0)] = 0.10
    critical_psi: Annotated[float, Field(gt=0.0, le=100.0)] = 0.25
    warning_mean_shift_z: Annotated[float, Field(gt=0.0, le=1000.0)] = 2.0
    critical_mean_shift_z: Annotated[float, Field(gt=0.0, le=1000.0)] = 4.0
    warning_missing_delta: Annotated[float, Field(ge=0.0, le=1.0)] = 0.05
    critical_missing_delta: Annotated[float, Field(ge=0.0, le=1.0)] = 0.15
    minimum_samples: Annotated[int, Field(ge=10, le=MAX_SAMPLES)] = 100

    @model_validator(mode="after")
    def ordered_thresholds(self) -> "DriftPolicy":
        if self.critical_psi < self.warning_psi:
            raise ValueError("critical_psi must be at least warning_psi")
        if self.critical_mean_shift_z < self.warning_mean_shift_z:
            raise ValueError("critical_mean_shift_z must be at least warning_mean_shift_z")
        if self.critical_missing_delta < self.warning_missing_delta:
            raise ValueError("critical_missing_delta must be at least warning_missing_delta")
        return self


class DriftBaselineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feature_name: Annotated[str, Field(min_length=1, max_length=160)]
    values: list[float | None] = Field(min_length=10, max_length=MAX_SAMPLES)
    bins: Annotated[int, Field(ge=2, le=100)] = 10
    policy: DriftPolicy = Field(default_factory=DriftPolicy)
    source_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None


class DriftBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_id: str
    feature_name: str
    created_at: datetime
    revision: int = Field(ge=1)
    sample_count: int
    finite_count: int
    missing_rate: float
    mean: float
    standard_deviation: float
    bin_edges: list[float]
    expected_proportions: list[float]
    policy: DriftPolicy
    source_sha256: str | None = None
    baseline_sha256: str


class DriftEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    values: list[float | None] = Field(min_length=1, max_length=MAX_SAMPLES)


class DriftEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_id: str
    feature_name: str
    severity: Literal["ok", "warning", "critical", "insufficient_data"]
    sample_count: int
    finite_count: int
    psi: float
    mean_shift_z: float
    missing_rate: float
    missing_rate_delta: float
    observed_proportions: list[float]
    violations: list[str]
    execution_authority_granted: bool = False


class DriftConflict(RuntimeError):
    pass


def _finite(values: list[float | None]) -> tuple[list[float], float]:
    finite: list[float] = []
    missing = 0
    for value in values:
        if value is None:
            missing += 1
            continue
        number = float(value)
        if not math.isfinite(number):
            missing += 1
            continue
        finite.append(number)
    return finite, missing / len(values)


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("at least one finite value is required")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def _edges(values: list[float], bins: int) -> list[float]:
    ordered = sorted(values)
    edges = [_quantile(ordered, index / bins) for index in range(1, bins)]
    deduplicated: list[float] = []
    for edge in edges:
        if not deduplicated or edge > deduplicated[-1]:
            deduplicated.append(edge)
    return deduplicated


def _proportions(values: list[float], edges: list[float]) -> list[float]:
    counts = [0] * (len(edges) + 1)
    for value in values:
        counts[bisect.bisect_right(edges, value)] += 1
    total = max(1, len(values))
    return [count / total for count in counts]


def _baseline_hash(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "baseline_sha256"}
    encoded = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_baseline(request: DriftBaselineCreate, *, now: datetime | None = None) -> DriftBaseline:
    finite, missing_rate = _finite(request.values)
    if len(finite) < request.policy.minimum_samples:
        raise ValueError("baseline does not contain the minimum number of finite samples")
    edges = _edges(finite, request.bins)
    proportions = _proportions(finite, edges)
    payload: dict[str, Any] = {
        "baseline_id": uuid4().hex,
        "feature_name": request.feature_name,
        "created_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "revision": 1,
        "sample_count": len(request.values),
        "finite_count": len(finite),
        "missing_rate": missing_rate,
        "mean": fmean(finite),
        "standard_deviation": pstdev(finite),
        "bin_edges": edges,
        "expected_proportions": proportions,
        "policy": request.policy.model_dump(mode="json"),
        "source_sha256": request.source_sha256,
    }
    payload["baseline_sha256"] = "0" * 64
    normalised = DriftBaseline.model_validate(payload).model_dump(mode="json")
    normalised["baseline_sha256"] = _baseline_hash(normalised)
    return DriftBaseline.model_validate(normalised)


def evaluate_drift(baseline: DriftBaseline, request: DriftEvaluationRequest) -> DriftEvaluation:
    if _baseline_hash(baseline.model_dump(mode="json")) != baseline.baseline_sha256:
        raise ValueError("drift baseline hash mismatch")
    finite, missing_rate = _finite(request.values)
    if len(finite) < baseline.policy.minimum_samples:
        return DriftEvaluation(
            baseline_id=baseline.baseline_id,
            feature_name=baseline.feature_name,
            severity="insufficient_data",
            sample_count=len(request.values),
            finite_count=len(finite),
            psi=0.0,
            mean_shift_z=0.0,
            missing_rate=missing_rate,
            missing_rate_delta=abs(missing_rate - baseline.missing_rate),
            observed_proportions=[0.0] * len(baseline.expected_proportions),
            violations=["minimum_samples"],
        )
    observed = _proportions(finite, baseline.bin_edges)
    psi = sum(
        (actual - expected) * math.log((actual + EPSILON) / (expected + EPSILON))
        for expected, actual in zip(baseline.expected_proportions, observed)
    )
    observed_mean = fmean(finite)
    if baseline.standard_deviation <= EPSILON:
        mean_shift_z = 0.0 if abs(observed_mean - baseline.mean) <= EPSILON else 1_000_000.0
    else:
        mean_shift_z = abs(observed_mean - baseline.mean) / baseline.standard_deviation
    missing_delta = abs(missing_rate - baseline.missing_rate)
    critical: list[str] = []
    warning: list[str] = []
    if psi >= baseline.policy.critical_psi:
        critical.append("psi")
    elif psi >= baseline.policy.warning_psi:
        warning.append("psi")
    if mean_shift_z >= baseline.policy.critical_mean_shift_z:
        critical.append("mean_shift")
    elif mean_shift_z >= baseline.policy.warning_mean_shift_z:
        warning.append("mean_shift")
    if missing_delta >= baseline.policy.critical_missing_delta:
        critical.append("missing_rate")
    elif missing_delta >= baseline.policy.warning_missing_delta:
        warning.append("missing_rate")
    severity: Literal["ok", "warning", "critical", "insufficient_data"]
    severity = "critical" if critical else "warning" if warning else "ok"
    return DriftEvaluation(
        baseline_id=baseline.baseline_id,
        feature_name=baseline.feature_name,
        severity=severity,
        sample_count=len(request.values),
        finite_count=len(finite),
        psi=max(0.0, psi),
        mean_shift_z=mean_shift_z,
        missing_rate=missing_rate,
        missing_rate_delta=missing_delta,
        observed_proportions=observed,
        violations=critical + warning,
    )


class DriftBaselineRepository:
    def __init__(self, path: Path | None = None, ledger: AuditLedger | None = None) -> None:
        self.path = path or resolve_data_home() / "drift-baselines.json"
        self.ledger = ledger or AuditLedger()
        self.lock = FileLock(str(self.path) + ".drift.lock")

    def _load(self) -> dict[str, Any]:
        document = read_json(self.path, {"schema_version": 1, "revision": 0, "baselines": {}, "audit_outbox": {}})
        if document.get("schema_version") != 1 or not isinstance(document.get("baselines"), dict):
            raise ValueError("invalid drift-baseline store")
        if len(document["baselines"]) > MAX_BASELINES:
            raise ValueError("drift-baseline limit exceeded")
        for baseline_id, raw in document["baselines"].items():
            baseline = DriftBaseline.model_validate(raw)
            if baseline.baseline_id != baseline_id or _baseline_hash(baseline.model_dump(mode="json")) != baseline.baseline_sha256:
                raise ValueError("stored drift baseline is invalid")
        ensure_audit_outbox(document)
        return document

    def create(
        self,
        request: DriftBaselineCreate,
        *,
        actor: str = "api-operator",
        expected_store_revision: int | None = None,
    ) -> DriftBaseline:
        baseline = build_baseline(request)
        with self.lock:
            document = self._load()
            if expected_store_revision is not None and document["revision"] != expected_store_revision:
                raise DriftConflict("drift-baseline store revision conflict")
            if len(document["baselines"]) >= MAX_BASELINES:
                raise DriftConflict("drift-baseline limit reached")
            document["revision"] += 1
            document["baselines"][baseline.baseline_id] = baseline.model_dump(mode="json")
            queue_audit_event(
                document,
                event_id=f"drift:{baseline.baseline_id}:created:{baseline.baseline_sha256[:16]}",
                event="monitoring.drift_baseline_created",
                actor=actor,
                subject=baseline.feature_name,
                details={
                    "baseline_id": baseline.baseline_id,
                    "baseline_sha256": baseline.baseline_sha256,
                    "sample_count": baseline.sample_count,
                    "store_revision": document["revision"],
                },
            )
            atomic_write_json(self.path, document)
        self.reconcile_audit()
        return baseline

    def get(self, baseline_id: str) -> DriftBaseline:
        with self.lock:
            raw = self._load()["baselines"].get(baseline_id)
            if raw is None:
                raise KeyError(baseline_id)
            return DriftBaseline.model_validate(raw)

    def list(self) -> list[DriftBaseline]:
        with self.lock:
            return [DriftBaseline.model_validate(value) for value in self._load()["baselines"].values()]

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
