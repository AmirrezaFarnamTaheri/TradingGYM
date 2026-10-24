import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from apps.api.example_runs import ExampleRunService, KIND, run_directory
from apps.example_worker import execute
from trading_gym.common.persistence import atomic_write_json
from trading_gym.experiments.models import ExperimentCreate, ExperimentState
from trading_gym.experiments.repository import ExperimentRepository, ExperimentConflict
from trading_gym.governance.ledger import AuditLedger


def service(tmp_path, **overrides):
    ledger = AuditLedger(tmp_path / "audit-ledger.jsonl", hmac_key=b"x" * 32)
    repository = ExperimentRepository(tmp_path / "experiments.json", ledger=ledger)
    options = dict(
        repository=repository,
        start=AsyncMock(
            return_value={"pid": 123, "create_time": 4.0, "command_sha256": "a" * 64}
        ),
        stop=AsyncMock(return_value={"remaining": 0}),
        status=AsyncMock(return_value={"status": "idle"}),
        identity_matches=lambda _: False,
        stop_identity=lambda _: {"remaining": 0},
    )
    options.update(overrides)
    return ExampleRunService(tmp_path, **options)


def test_exact_claim_preserves_unrelated_pending_job(tmp_path):
    svc = service(tmp_path)
    other = svc.repository.create(
        ExperimentCreate(
            experiment_type="backtest",
            name="other",
            configuration={},
            idempotency_key="unrelated-first",
            priority=100,
        )
    )
    run = asyncio.run(svc.start("reference-fixture-001"))
    result = execute(
        run["run_id"], home=tmp_path, replay=lambda: {"metrics": {"return": 0.1}}
    )
    assert svc.repository.get(other.experiment_id).state is ExperimentState.PENDING
    assert result["sha256"]
    assert (
        svc.repository.audit_pending() > 0
    )  # Worker never receives sealing credentials.
    loaded = asyncio.run(svc.artifact(run["run_id"]))
    assert loaded["metrics"]["return"] == 0.1
    assert asyncio.run(svc.get(run["run_id"]))["audit_delivery_pending"] == 0
    assert svc.repository.ledger.verify()["valid"]


def test_retry_and_restart_never_duplicate_launch(tmp_path):
    svc = service(tmp_path)
    first = asyncio.run(svc.start("same-key-across-restart"))
    restarted = service(tmp_path)
    second = asyncio.run(restarted.start("same-key-across-restart"))
    assert first["run_id"] == second["run_id"]
    restarted.start_process.assert_not_called()
    assert second["artifact_available"] is False
    with pytest.raises(ExperimentConflict, match="not available"):
        asyncio.run(restarted.artifact(second["run_id"]))


def test_lost_lease_never_publishes_candidate(tmp_path):
    svc = service(tmp_path)
    run_id = asyncio.run(svc.start("expired-worker-fixture"))["run_id"]

    def expire_during_work():
        current = svc.repository.get(run_id)
        future = current.lease_expires_at + timedelta(seconds=1)
        original = svc.repository._now
        svc.repository._now = lambda: future
        assert svc.repository.get(run_id).state is ExperimentState.FAILED
        svc.repository._now = original
        return {"metrics": {}}

    with pytest.raises(ExperimentConflict):
        execute(run_id, home=tmp_path, replay=expire_during_work)
    assert not asyncio.run(svc.get(run_id))["artifact_available"]
    assert (run_directory(tmp_path, run_id) / "result.json").exists()


def test_cancel_does_not_claim_unobserved_exit_or_kill_reused_pid(tmp_path):
    svc = service(
        tmp_path,
        identity_matches=lambda _: True,
        stop_identity=lambda _: {"remaining": 1},
    )
    run_id = asyncio.run(svc.start("cancel-not-exited-yet"))["run_id"]
    with pytest.raises(ExperimentConflict, match="exit remains unverified"):
        asyncio.run(svc.cancel(run_id))
    assert svc.repository.get(run_id).state is ExperimentState.PENDING
    assert asyncio.run(svc.get(run_id))["cancellation_requested"]
    svc.identity_matches = lambda _: False  # Original identity exited or PID reused.
    svc.stop_identity = lambda _: pytest.fail("must not signal a reused PID")
    assert asyncio.run(svc.cancel(run_id))["state"] == "cancelled"


def test_artifact_corruption_fails_admission(tmp_path):
    svc = service(tmp_path)
    run_id = asyncio.run(svc.start("artifact-check-fixture"))["run_id"]
    execute(run_id, home=tmp_path, replay=lambda: {"metrics": {}})
    atomic_write_json(
        run_directory(tmp_path, run_id) / "result.json",
        {"run_id": run_id, "kind": KIND},
    )
    with pytest.raises(ExperimentConflict, match="digest failed"):
        asyncio.run(svc.artifact(run_id))


def test_real_reference_replay_is_deterministic_and_scoped(tmp_path):
    from apps.example_worker import reference_replay

    one, two = reference_replay(), reference_replay()
    assert one == two
    assert one["bars"] == 96 and one["trade_count"] > 0
    assert one["qualification"] == "workflow_evidence_only"


def test_launch_uncertainty_and_recovered_identity_are_explicit(tmp_path):
    svc = service(
        tmp_path, start=AsyncMock(side_effect=RuntimeError("lost registration"))
    )
    with pytest.raises(RuntimeError):
        asyncio.run(svc.start("launch-result-unknown"))
    restarted = service(tmp_path)
    run = asyncio.run(restarted.start("launch-result-unknown"))
    assert run["phase"] == "launch_unresolved"
    restarted.start_process.assert_not_called()
    with pytest.raises(ExperimentConflict, match="launch outcome is unresolved"):
        asyncio.run(restarted.cancel(run["run_id"]))
    other = service(tmp_path, identity_matches=lambda _: True)
    known = asyncio.run(other.start("verified-recovery-identity"))
    recovered = asyncio.run(other.recover_identities())
    assert recovered[f"example_{known['run_id']}"]["recovered"] is True


def test_saved_run_list_uses_one_repository_listing(tmp_path, monkeypatch):
    svc = service(tmp_path)
    first = asyncio.run(svc.start("list-fixture-first"))["run_id"]
    second = asyncio.run(svc.start("list-fixture-second"))["run_id"]
    monkeypatch.setattr(
        svc.repository, "get", lambda _: pytest.fail("per-row store reload")
    )
    listed = asyncio.run(svc.list())
    assert [row["run_id"] for row in listed] == [second, first]
