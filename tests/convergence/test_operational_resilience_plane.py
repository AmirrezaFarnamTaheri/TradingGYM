from __future__ import annotations


import pytest

from trading_gym.governance.ledger import AuditLedger
from trading_gym.operations.resilience import SnapshotService
from trading_gym.operations.slo import (
    SLOObservationWindow,
    ServiceLevelObjective,
    evaluate_slo,
)


def test_snapshot_create_verify_plan_and_tamper_detection(tmp_path):
    source = tmp_path / "state"
    source.mkdir()
    (source / "governance.json").write_text('{"revision": 3}\n', encoding="utf-8")
    nested = source / "models"
    nested.mkdir()
    (nested / "active.json").write_text('{"version": "v1"}\n', encoding="utf-8")
    service = SnapshotService(
        source_root=source,
        backup_root=tmp_path / "backups",
        ledger=AuditLedger(tmp_path / "audit.jsonl"),
        hmac_key=b"x" * 32,
    )
    manifest = service.create(
        ["governance.json", "models"], snapshot_id="snapshot-test-001"
    )
    verified = service.verify(manifest.snapshot_id)
    assert verified["valid"] and verified["hmac_valid"]
    plan = service.plan_restore(manifest.snapshot_id)
    assert plan.unchanged == 2 and not plan.mutation_performed

    (source / "governance.json").write_text('{"revision": 4}\n', encoding="utf-8")
    plan = service.plan_restore(manifest.snapshot_id)
    assert plan.replacements == 1
    backup_file = (
        tmp_path / "backups" / manifest.snapshot_id / "data" / "models" / "active.json"
    )
    backup_file.write_text("tampered", encoding="utf-8")
    assert not service.verify(manifest.snapshot_id)["valid"]


def test_snapshot_rejects_runtime_and_secret_surfaces(tmp_path):
    source = tmp_path / "state"
    (source / "runtime").mkdir(parents=True)
    (source / "runtime" / "token.json").write_text("{}", encoding="utf-8")
    service = SnapshotService(
        source_root=source,
        backup_root=tmp_path / "backups",
        ledger=AuditLedger(tmp_path / "audit.jsonl"),
    )
    with pytest.raises(ValueError, match="runtime or secret"):
        service.create(["runtime"])


def test_slo_error_budget_and_latency_evaluation():
    objective = ServiceLevelObjective(
        name="order-admission",
        target_success_fraction=0.99,
        target_latency_fraction=0.95,
        latency_threshold_ms=100,
        minimum_events=100,
    )
    healthy = evaluate_slo(
        objective,
        SLOObservationWindow(
            total_events=1000,
            successful_events=995,
            latency_ms=[50.0] * 990 + [150.0] * 10,
        ),
    )
    breached = evaluate_slo(
        objective,
        SLOObservationWindow(
            total_events=1000, successful_events=970, latency_ms=[150.0] * 1000
        ),
    )
    assert healthy.status in {"healthy", "at_risk"}
    assert breached.status == "breached"
    assert set(breached.violations) == {"success_fraction", "latency_fraction"}


def test_slo_rejects_partial_latency_samples():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="latency observations must match"):
        SLOObservationWindow(total_events=2, successful_events=2, latency_ms=[1.0])
