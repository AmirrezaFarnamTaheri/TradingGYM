"""Fixed, credential-free reference replay worker; no exchange or model training.

Owns the ExperimentRepository lease. Audit events remain in its durable outbox
for the credential-bearing API to seal; no audit/provider/broker key is needed.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import threading

from apps.api.example_runs import KIND, MAX_ARTIFACT_BYTES, run_directory
from trading_gym.common.persistence import atomic_write_json, read_json, resolve_data_home
from trading_gym.experiments.repository import ExperimentRepository, ExperimentConflict


class DeferredAuditSink:
    def append(self, **_kwargs):
        raise RuntimeError("audit delivery belongs to the API reconciler")


class WorkerExperimentRepository(ExperimentRepository):
    def reconcile_audit(self) -> dict[str, int]:
        # Every transition still queues its normal event atomically. The worker
        # cannot seal the shared audit ledger and must not claim delivery.
        return {"delivered": 0, "pending": self.audit_pending()}


def reference_replay() -> dict:
    import pandas as pd
    from trading_gym.playground.runner import PlaygroundRunSpec, run_playground
    from trading_gym.strategy_lab.models import StrategySpec
    close = [100 + i / 20 + math.sin(i / 5) for i in range(96)]
    frame = pd.DataFrame({"open": close, "high": [x + .2 for x in close],
        "low": [x - .2 for x in close], "close": close, "volume": [100.] * 96,
        "spread_bps": [2.] * 96}, index=pd.date_range("2024-01-01", periods=96, freq="5min", tz="UTC"))
    strategy = StrategySpec.model_validate({"name": "reference-sma-fixture", "timeframe": "5m",
        "indicators": [{"name": "sma", "alias": "fast", "params": {"period": 5}}],
        "entry": {"conditions": [{"left": {"kind": "field", "value": "close"},
            "operator": "gt", "right": {"kind": "indicator", "value": "fast"}}]},
        "exit": {"conditions": [{"left": {"kind": "field", "value": "close"},
            "operator": "lt", "right": {"kind": "indicator", "value": "fast"}}]}})
    result = run_playground(PlaygroundRunSpec(window=frame, strategy=strategy, fee_bps=10.))
    return {"manifest": result.manifest, "metrics": result.metrics,
            "bars": len(frame), "trade_count": len(result.trades),
            "data_kind": "synthetic_fixture", "costs_kind": "modeled_fee_and_spread",
            "source_revision": os.getenv("TRADING_GYM_BUILD_REVISION", "unrecorded"),
            "qualification": "workflow_evidence_only"}


def execute(run_id: str, *, home=None, replay=reference_replay) -> dict:
    home = home or resolve_data_home()
    directory = run_directory(home, run_id)
    manifest = read_json(directory / "launch.json", {})
    if manifest.get("run_id") != run_id or manifest.get("cancellation_requested"):
        raise ExperimentConflict("launch not admitted or cancellation requested")
    repository = WorkerExperimentRepository(home / "experiments.json", ledger=DeferredAuditSink())
    record = repository.get(run_id)
    if record.configuration.get("kind") != KIND:
        raise ExperimentConflict("unsupported worker configuration")
    worker_id = f"example-{run_id}"
    record = repository.claim_next(worker_id=worker_id, experiment_id=run_id,
                                   accepted_types={"backtest"}, lease_seconds=120)
    if record is None:
        raise ExperimentConflict("run is not claimable")
    state = {"revision": record.revision, "error": None}
    revision_lock, stopped = threading.Lock(), threading.Event()

    def heartbeat():
        while not stopped.wait(15):
            with revision_lock:
                try:
                    updated = repository.heartbeat(run_id, worker_id=worker_id,
                        expected_revision=state["revision"], lease_seconds=120)
                    state["revision"] = updated.revision
                except Exception as exc:
                    state["error"] = exc
                    return

    thread = threading.Thread(target=heartbeat, name="reference-run-lease", daemon=True)
    thread.start()
    try:
        output = {**replay(), "schema_version": 1, "kind": KIND, "run_id": run_id}
        with revision_lock:
            if state["error"]:
                raise ExperimentConflict("worker lease heartbeat failed")
            if read_json(directory / "launch.json", {}).get("cancellation_requested"):
                raise ExperimentConflict("cancellation requested before artifact admission")
            path = directory / "result.json"
            if path.exists():
                raise ExperimentConflict("candidate artifact already exists")
            # Reserve path before spawn; write the completed candidate before
            # the compare-and-set completion. An orphan candidate is unpublished.
            atomic_write_json(path, output)
            raw = path.read_bytes()
            if len(raw) > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact too large")
            result = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                      "artifact": "result.json", "kind": KIND}
            repository.complete(run_id, worker_id=worker_id,
                                expected_revision=state["revision"], result=result)
            return result
    except Exception:
        with revision_lock:
            try:
                repository.fail(run_id, worker_id=worker_id,
                    expected_revision=state["revision"], error="reference replay did not complete", retryable=False)
            except ExperimentConflict:
                pass
        raise
    finally:
        stopped.set()
        thread.join(timeout=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    # Hard lifetime for this fixed workload, independent of API polling.
    # A timeout leaves the lease/candidate for failure reconciliation.
    watchdog = threading.Timer(60, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    try:
        execute(args.run_id)
    finally:
        watchdog.cancel()
