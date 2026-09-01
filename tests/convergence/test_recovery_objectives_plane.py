from datetime import datetime, timedelta, timezone

from trading_gym.operations import RestoreDrillReport
from trading_gym.operations.recovery_objectives import (
    RecoveryDrillEvidence,
    RecoveryObjectivePolicy,
    RecoveryObjectiveRequest,
    RecoverySnapshotEvidence,
    evaluate_recovery_objectives,
)

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


def snapshot(*, store="orders", age_seconds=300, valid=True, off_host=True):
    return RecoverySnapshotEvidence(
        snapshot_id=f"snapshot:{store}-001",
        captured_at=NOW - timedelta(seconds=age_seconds),
        verified_at=NOW - timedelta(seconds=max(0, age_seconds - 10)),
        valid=valid,
        off_host_copy=off_host,
        covered_stores=[store],
        manifest_sha256="a" * 64,
    )


def drill(*, store="orders", age_seconds=600, duration_ms=5000, valid=True):
    return RecoveryDrillEvidence(
        completed_at=NOW - timedelta(seconds=age_seconds),
        covered_stores=[store],
        report=RestoreDrillReport(
            snapshot_id=f"snapshot:{store}-001",
            valid=valid,
            files_verified=3,
            bytes_verified=100,
            duration_ms=duration_ms,
            failures=[] if valid else ["hash mismatch"],
        ),
    )


def policy(stores=None):
    return RecoveryObjectivePolicy(
        required_stores=stores or ["orders"],
        maximum_rpo_seconds=900,
        maximum_rto_seconds=30,
        maximum_drill_age_seconds=3600,
        require_off_host_copy=True,
    )


def test_recovery_objectives_pass_with_fresh_off_host_snapshot_and_drill():
    report = evaluate_recovery_objectives(RecoveryObjectiveRequest(
        evaluated_at=NOW,
        snapshots=[snapshot()],
        drills=[drill()],
        policy=policy(),
    ))
    assert report.ready_for_human_review is True
    assert report.stores[0].rpo_seconds == 300
    assert report.restore_authority_granted is False
    assert report.failover_authority_granted is False


def test_missing_store_evidence_fails_closed():
    report = evaluate_recovery_objectives(RecoveryObjectiveRequest(
        evaluated_at=NOW,
        snapshots=[snapshot(store="orders")],
        drills=[drill(store="orders")],
        policy=policy(["orders", "accounting"]),
    ))
    assert report.ready_for_human_review is False
    assert "valid_snapshot_missing:accounting" in report.blockers
    assert "valid_restore_drill_missing:accounting" in report.blockers


def test_stale_snapshot_slow_drill_and_missing_off_host_copy_are_blockers():
    report = evaluate_recovery_objectives(RecoveryObjectiveRequest(
        evaluated_at=NOW,
        snapshots=[snapshot(age_seconds=1200, off_host=False)],
        drills=[drill(duration_ms=60_000)],
        policy=policy(),
    ))
    assert report.ready_for_human_review is False
    assert "rpo_exceeded:orders" in report.blockers
    assert "off_host_copy_missing:orders" in report.blockers
    assert "rto_exceeded:orders" in report.blockers


def test_restore_mutation_is_not_accepted_as_drill_evidence():
    bad = drill().model_copy(update={
        "report": drill().report.model_copy(update={"restore_mutation_performed": True}),
    })
    report = evaluate_recovery_objectives(RecoveryObjectiveRequest(
        evaluated_at=NOW,
        snapshots=[snapshot()],
        drills=[bad],
        policy=policy(),
    ))
    assert "valid_restore_drill_missing:orders" in report.blockers
