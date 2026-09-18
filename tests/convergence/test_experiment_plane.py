from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_gym.experiments import (
    ExperimentConflict,
    ExperimentCreate,
    ExperimentRepository,
    ExperimentState,
)


def request(key: str = "experiment-idempotency-001") -> ExperimentCreate:
    return ExperimentCreate(
        experiment_type="backtest",
        name="Walk-forward validation",
        configuration={"secret": "redact-me", "folds": 5},
        idempotency_key=key,
        max_attempts=2,
    )


def test_experiment_lifecycle_is_idempotent_and_lease_owned(tmp_path: Path) -> None:
    repository = ExperimentRepository(tmp_path / "experiments.json")
    created = repository.create(request())
    duplicate = repository.create(request())
    assert duplicate.experiment_id == created.experiment_id
    with pytest.raises(ExperimentConflict):
        repository.create(
            ExperimentCreate(
                experiment_type="research",
                name="Different job",
                configuration={},
                idempotency_key="experiment-idempotency-001",
            )
        )
    assert "redact-me" not in (tmp_path / "experiments.json").read_text()

    claimed = repository.claim_next(worker_id="worker-a", lease_seconds=60)
    assert claimed is not None and claimed.state is ExperimentState.RUNNING
    with pytest.raises(ExperimentConflict):
        repository.heartbeat(
            claimed.experiment_id,
            worker_id="worker-b",
            expected_revision=claimed.revision,
        )
    heartbeat = repository.heartbeat(
        claimed.experiment_id,
        worker_id="worker-a",
        expected_revision=claimed.revision,
        lease_seconds=120,
    )
    completed = repository.complete(
        heartbeat.experiment_id,
        worker_id="worker-a",
        expected_revision=heartbeat.revision,
        result={"score": 0.7, "api_key": "hidden"},
    )
    assert completed.state is ExperimentState.SUCCEEDED
    assert completed.claimed_by is None
    assert completed.lease_expires_at is None
    assert completed.result == {"score": 0.7, "api_key": "***"}


def test_expired_lease_is_reconciled_and_reclaimed(tmp_path: Path) -> None:
    path = tmp_path / "experiments.json"
    repository = ExperimentRepository(path)
    created = repository.create(request("experiment-idempotency-002"))
    claimed = repository.claim_next(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None

    document = json.loads(path.read_text())
    raw = document["experiments"][created.experiment_id]
    raw["lease_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    path.write_text(json.dumps(document))

    reconciled = repository.get(created.experiment_id)
    assert reconciled.state is ExperimentState.STALE
    reclaimed = repository.claim_next(worker_id="worker-b", lease_seconds=30)
    assert reclaimed is not None
    assert reclaimed.claimed_by == "worker-b"
    assert reclaimed.attempts == 2


def test_cancel_is_terminal_and_revision_checked(tmp_path: Path) -> None:
    repository = ExperimentRepository(tmp_path / "experiments.json")
    created = repository.create(request("experiment-idempotency-003"))
    with pytest.raises(ExperimentConflict):
        repository.cancel(created.experiment_id, expected_revision=99, reason="stale")
    cancelled = repository.cancel(
        created.experiment_id,
        expected_revision=created.revision,
        reason="operator stop",
    )
    assert cancelled.state is ExperimentState.CANCELLED
    with pytest.raises(ExperimentConflict):
        repository.cancel(
            created.experiment_id,
            expected_revision=cancelled.revision,
            reason="again",
        )


def test_retryable_failure_requeues_until_attempt_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    repository = ExperimentRepository(tmp_path / "experiments.json")
    created = repository.create(request("experiment-idempotency-004"))
    claimed = repository.claim_next(worker_id="worker-a", lease_seconds=60)
    assert claimed is not None
    retry = repository.fail(
        created.experiment_id,
        worker_id="worker-a",
        expected_revision=claimed.revision,
        error="temporary provider failure",
        retryable=True,
    )
    assert retry.state is ExperimentState.PENDING
    reclaimed = repository.claim_next(worker_id="worker-b", lease_seconds=60)
    assert reclaimed is not None and reclaimed.attempts == 2
    final = repository.fail(
        created.experiment_id,
        worker_id="worker-b",
        expected_revision=reclaimed.revision,
        error="still failing",
        retryable=True,
    )
    assert final.state is ExperimentState.FAILED


def test_higher_priority_job_is_claimed_first(tmp_path: Path) -> None:
    repository = ExperimentRepository(tmp_path / "experiments.json")
    low = request("experiment-priority-low").model_copy(update={"priority": 10})
    high = request("experiment-priority-high").model_copy(update={"priority": 90})
    repository.create(low)
    high_record = repository.create(high)
    claimed = repository.claim_next(worker_id="worker", lease_seconds=60)
    assert claimed is not None
    assert claimed.experiment_id == high_record.experiment_id


def test_experiment_list_uses_same_high_priority_first_order_as_claiming(
    tmp_path: Path,
) -> None:
    repository = ExperimentRepository(tmp_path / "experiments.json")
    repository.create(
        ExperimentCreate(
            experiment_type="research",
            name="low priority",
            configuration={},
            idempotency_key="priority-low-001",
            priority=10,
        )
    )
    repository.create(
        ExperimentCreate(
            experiment_type="research",
            name="high priority",
            configuration={},
            idempotency_key="priority-high-001",
            priority=90,
        )
    )
    assert [item.priority for item in repository.list()] == [90, 10]
