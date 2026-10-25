from __future__ import annotations

import json

import pytest

from trading_gym.execution.governance.ledger import AuditLedger
from trading_gym.core.monitoring.drift import (
    DriftBaselineCreate,
    DriftBaselineRepository,
    DriftEvaluationRequest,
    DriftPolicy,
    build_baseline,
    evaluate_drift,
)


def baseline_request():
    values = [float(index % 20) for index in range(500)]
    return DriftBaselineCreate(
        feature_name="returns_5m",
        values=values,
        bins=10,
        policy=DriftPolicy(minimum_samples=50),
        source_sha256="a" * 64,
    )


def test_drift_baseline_and_shift_detection():
    baseline = build_baseline(baseline_request())
    stable = evaluate_drift(
        baseline,
        DriftEvaluationRequest(values=[float(index % 20) for index in range(500)]),
    )
    shifted = evaluate_drift(
        baseline,
        DriftEvaluationRequest(
            values=[100.0 + float(index % 5) for index in range(500)]
        ),
    )
    assert stable.severity == "ok"
    assert shifted.severity == "critical"
    assert "psi" in shifted.violations
    assert not shifted.execution_authority_granted


def test_drift_insufficient_samples_and_repository_cas(tmp_path):
    baseline = build_baseline(baseline_request())
    result = evaluate_drift(baseline, DriftEvaluationRequest(values=[1.0] * 10))
    assert result.severity == "insufficient_data"
    repo = DriftBaselineRepository(
        tmp_path / "drift.json", AuditLedger(tmp_path / "audit.jsonl")
    )
    saved = repo.create(baseline_request(), expected_store_revision=0)
    assert repo.get(saved.baseline_id) == saved
    with pytest.raises(RuntimeError, match="revision"):
        repo.create(baseline_request(), expected_store_revision=0)


def test_drift_store_detects_baseline_tampering(tmp_path):
    repo = DriftBaselineRepository(
        tmp_path / "drift.json", AuditLedger(tmp_path / "audit.jsonl")
    )
    saved = repo.create(baseline_request())
    raw = json.loads((tmp_path / "drift.json").read_text(encoding="utf-8"))
    raw["baselines"][saved.baseline_id]["mean"] = 999.0
    (tmp_path / "drift.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        repo.list()
