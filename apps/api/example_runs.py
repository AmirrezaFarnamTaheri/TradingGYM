"""Durable bridge from one bounded reference workload to the existing supervisor.

The launch intent is persisted before spawning. Repeating an idempotency key
returns that run, even after a supervisor restart; uncertain launches are never
silently retried. The worker owns the existing experiment lease and completion.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

from filelock import FileLock
from trading_gym.common.audit_outbox import AuditOutboxPending
from trading_gym.common.persistence import atomic_write_json, read_json
from trading_gym.common.security import confined_child
from trading_gym.experiments.models import ExperimentCreate, ExperimentState
from trading_gym.experiments.repository import ExperimentRepository, ExperimentConflict

KIND = "reference-replay-v1"
MAX_ARTIFACT_BYTES = 512 * 1024


def run_directory(home: Path, run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("invalid run identifier")
    return confined_child(home, "example-runs", run_id, allow_symlinks=False)


def read_artifact(home: Path, record) -> dict[str, Any]:
    if record.state is not ExperimentState.SUCCEEDED or not record.result:
        raise ExperimentConflict("completed artifact is not available")
    path = confined_child(run_directory(home, record.experiment_id), "result.json",
                          must_exist=True, allow_symlinks=False)
    if not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ExperimentConflict("artifact bounds failed")
    raw = path.read_bytes()
    if len(raw) > MAX_ARTIFACT_BYTES or hashlib.sha256(raw).hexdigest() != record.result.get("sha256"):
        raise ExperimentConflict("artifact digest failed")
    document = json.loads(raw)
    if document.get("run_id") != record.experiment_id or document.get("kind") != KIND:
        raise ExperimentConflict("artifact identity failed")
    return document


class ExampleRunService:
    def __init__(self, home: Path, *, repository: ExperimentRepository,
                 start, stop, status, identity_matches, stop_identity) -> None:
        self.home, self.repository = home, repository
        self.start_process, self.stop_process, self.process_status = start, stop, status
        self.identity_matches, self.stop_identity = identity_matches, stop_identity

    def _record(self, run_id: str):
        run_directory(self.home, run_id)
        record = self.repository.get(run_id)
        if record.configuration.get("kind") != KIND:
            raise KeyError(run_id)
        return record

    def _manifest(self, run_id: str) -> dict:
        directory = run_directory(self.home, run_id)
        return read_json(directory / "launch.json", {}) if directory.exists() else {}

    def _prepare(self, idempotency_key: str) -> tuple[str, bool]:
        record = self.repository.create(ExperimentCreate(experiment_type="backtest",
            name="Credential-free reference replay", configuration={"kind": KIND},
            idempotency_key=idempotency_key, max_attempts=1))
        if record.state is not ExperimentState.PENDING:
            return record.experiment_id, False
        directory = run_directory(self.home, record.experiment_id)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(directory / "bridge.lock")):
            if (directory / "launch.json").exists():
                return record.experiment_id, False
            atomic_write_json(directory / "launch.json", {
                "schema_version": 1, "run_id": record.experiment_id, "phase": "launching",
                "process_key": f"example_{record.experiment_id}", "process": None,
                "artifact_name": "result.json", "cancellation_requested": False})
        return record.experiment_id, True

    def _update_manifest(self, run_id: str, **changes) -> None:
        directory = run_directory(self.home, run_id)
        with FileLock(str(directory / "bridge.lock")):
            document = self._manifest(run_id)
            document.update(changes)
            atomic_write_json(directory / "launch.json", document)

    async def start(self, idempotency_key: str) -> dict:
        run_id, should_spawn = await asyncio.to_thread(self._prepare, idempotency_key)
        if should_spawn:
            command = [sys.executable, "-m", "apps.example_worker", "--run-id", run_id]
            try:
                process = await self.start_process(f"example_{run_id}", command, source="EXAMPLE",
                    metadata={"type": "example", "run_id": run_id},
                    extra_environment={"TRADING_GYM_HOME": str(self.home)})
                await asyncio.to_thread(self._update_manifest, run_id,
                                        phase="spawned", process=process)
            except (Exception, asyncio.CancelledError):
                # A supervisor exception can follow an observed child start.
                # Keep the intent unresolved; never infer that no process ran.
                await asyncio.to_thread(self._update_manifest, run_id, phase="launch_unresolved")
                raise
        return await self.get(run_id)

    async def get(self, run_id: str) -> dict:
        record = await asyncio.to_thread(self._record, run_id)
        return await self._view(record, await self._audit_pending())

    async def _view(self, record, audit_pending: int) -> dict:
        run_id = record.experiment_id
        manifest = await asyncio.to_thread(self._manifest, run_id)
        process = manifest.get("process")
        alive = bool(process and await asyncio.to_thread(self.identity_matches, process))
        return {"run_id": run_id, "state": record.state.value, "revision": record.revision,
                "phase": manifest.get("phase", "launch_unresolved"),
                "worker_identity_verified": alive,
                "cancellation_requested": manifest.get("cancellation_requested", False),
                "artifact_available": record.state is ExperimentState.SUCCEEDED,
                "result": record.result, "error": record.error,
                "audit_delivery_pending": audit_pending,
                "scope": "synthetic_reference_replay_not_paper_or_live"}

    async def _audit_pending(self) -> int:
        try:
            return (await asyncio.to_thread(self.repository.reconcile_audit))["pending"]
        except AuditOutboxPending:
            return await asyncio.to_thread(self.repository.audit_pending)

    async def list(self) -> list[dict]:
        records = await asyncio.to_thread(self.repository.list)
        selected = [r for r in records if r.configuration.get("kind") == KIND]
        selected.sort(key=lambda r: r.created_at, reverse=True)
        # One bounded store read/reconciliation, not a whole-store reload for
        # every row whenever the journey reopens its saved-run list.
        pending = await self._audit_pending()
        return [await self._view(record, pending) for record in selected[:50]]

    async def artifact(self, run_id: str) -> dict:
        record = await asyncio.to_thread(self._record, run_id)
        return await asyncio.to_thread(read_artifact, self.home, record)

    async def recover_identities(self) -> dict[str, dict]:
        """Adopt verified survivors into the supervisor quota after restart."""
        records = await asyncio.to_thread(self.repository.list)
        recovered = {}
        for record in records:
            if record.configuration.get("kind") != KIND:
                continue
            manifest = await asyncio.to_thread(self._manifest, record.experiment_id)
            identity = manifest.get("process")
            if identity and await asyncio.to_thread(self.identity_matches, identity):
                recovered[f"example_{record.experiment_id}"] = {
                    **identity, "status": "running", "type": "example",
                    "run_id": record.experiment_id, "recovered": True}
        return recovered

    async def cancel(self, run_id: str) -> dict:
        record = await asyncio.to_thread(self._record, run_id)
        if record.state in {ExperimentState.SUCCEEDED, ExperimentState.FAILED, ExperimentState.CANCELLED}:
            return await self.get(run_id)
        await asyncio.to_thread(self._update_manifest, run_id, cancellation_requested=True)
        manifest = await asyncio.to_thread(self._manifest, run_id)
        identity = manifest.get("process")
        if not identity:
            raise ExperimentConflict("launch outcome is unresolved; no unverified PID will be signalled")
        if await asyncio.to_thread(self.identity_matches, identity):
            current = await self.process_status(manifest["process_key"])
            if current.get("pid") == identity.get("pid") and current.get("create_time") == identity.get("create_time"):
                result = await self.stop_process(manifest["process_key"])
            else:
                # Restart reconciliation reuses the supervisor's identity check
                # and tree termination, never a bare PID from a request.
                result = await asyncio.to_thread(self.stop_identity, identity)
            if result.get("remaining") != 0:
                raise ExperimentConflict("cancellation requested; worker exit remains unverified")
        record = await asyncio.to_thread(self._record, run_id)
        if record.state not in {ExperimentState.SUCCEEDED, ExperimentState.FAILED, ExperimentState.CANCELLED}:
            await asyncio.to_thread(self.repository.cancel, run_id,
                expected_revision=record.revision, reason="operator cancelled reference replay")
        return await self.get(run_id)
