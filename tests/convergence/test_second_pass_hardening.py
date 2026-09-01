from __future__ import annotations

import json
import mmap
import os
import struct
import subprocess
import sys
import time
import asyncio
from pathlib import Path

import pytest

from apps.api.runtime import (
    BoundedLogBuffer,
    capture_process_identity,
    child_environment,
    decode_websocket_auth_protocol,
    process_identity_matches,
    terminate_process_tree,
)
from apps.api import main as api_main
from apps.common.ipc import MetricsReader, MetricsWriter
from apps.common.runtime_files import (
    clear_panic_signal,
    handle_panic_request,
    panic_requested,
    write_heartbeat,
)
from trading_gym.common.config import BreakerConfig
from trading_gym.common.persistence import atomic_write_json, read_json
from trading_gym.common.security import confined_child, validate_base_url
from trading_gym.governance.ledger import AuditLedger
from trading_gym.governance.models import DecisionRequest, ProposalCreate, RiskTier
from trading_gym.governance.service import (
    GovernanceAuditPending,
    GovernanceConflict,
    GovernanceService,
)
from trading_gym.governance.triggers import TriggerCreate, TriggerKind, TriggerRepository
from trading_gym.models.versioning import ModelRegistry
from trading_gym.risk.circuit_breakers import BreakerState, CircuitBreaker


class FlakyLedger(AuditLedger):
    def __init__(self, path: Path, *, hmac_key: str = "z" * 32) -> None:
        super().__init__(path, hmac_key=hmac_key)
        self.fail = True

    def append(self, **kwargs):  # type: ignore[no-untyped-def]
        if self.fail:
            raise OSError("simulated ledger outage")
        return super().append(**kwargs)


def test_atomic_json_rejects_symlink_targets(tmp_path: Path) -> None:
    victim = tmp_path / "victim.json"
    atomic_write_json(victim, {"safe": True})
    link = tmp_path / "state.json"
    link.symlink_to(victim)
    with pytest.raises(ValueError, match="symlink"):
        atomic_write_json(link, {"safe": False})
    with pytest.raises(ValueError, match="symlink"):
        read_json(link, {})
    assert read_json(victim, {}) == {"safe": True}


def test_confined_child_rejects_traversal_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        confined_child(root, "..", "outside")
    with pytest.raises(ValueError, match="escapes|symlink"):
        confined_child(root, "escape", "file.txt")


def test_network_validator_rejects_ipv4_mapped_loopback() -> None:
    with pytest.raises(ValueError, match="disallowed"):
        validate_base_url("http://[::ffff:127.0.0.1]:8080", resolve_dns=False)


def test_metrics_envelope_detects_tampering_and_staleness(tmp_path: Path) -> None:
    path = tmp_path / "metrics.dat"
    writer = MetricsWriter(path, size=4096)
    assert writer.write({"equity": 100.0}) is True
    assert MetricsReader(path, size=4096).read() == {"equity": 100.0}

    envelope = json.loads(path.read_text())
    envelope["payload"]["equity"] = 1_000_000.0
    path.write_text(json.dumps(envelope))
    assert MetricsReader(path, size=4096).read() == {}

    writer.write({"equity": 101.0})
    envelope = json.loads(path.read_text())
    envelope["written_at"] = time.time() - 10
    path.write_text(json.dumps(envelope))
    reader = MetricsReader(path, size=4096, max_age_seconds=1)
    assert reader.read() == {}
    assert reader.last_error == "stale"


def test_metrics_reader_supports_legacy_mmap_during_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "legacy.dat"
    size = 1024
    payload = json.dumps({"legacy": True}).encode()
    path.write_bytes(b"\0" * size)
    with path.open("r+b") as handle, mmap.mmap(handle.fileno(), size) as mm:
        mm.write(struct.pack(">I", len(payload)))
        mm.write(payload)
    assert MetricsReader(path, size=size).read() == {"legacy": True}


def test_runtime_control_files_are_atomic_and_retryable(tmp_path: Path) -> None:
    heartbeat = tmp_path / "health.heartbeat"
    panic = tmp_path / "panic.signal"
    assert write_heartbeat(heartbeat, timestamp=123.5) == 123.5
    assert heartbeat.read_text() == "123.500000\n"
    atomic_write_json(tmp_path / "unrelated.json", {"ok": True})
    panic.write_text("PANIC_TRIGGERED_AT_123\n")
    assert panic_requested(panic) is True
    assert clear_panic_signal(panic) is True
    assert clear_panic_signal(panic) is False


def test_model_registry_rebuilds_index_and_rejects_tampering(tmp_path: Path) -> None:
    source = tmp_path / "model.zip"
    source.write_bytes(b"model-bytes")
    root = tmp_path / "registry"
    registry = ModelRegistry(root)
    registry.register("v1", source, {"sharpe": 1.2}, {"seed": 7})
    (root / "versions.json").unlink()
    rebuilt = ModelRegistry(root)
    assert rebuilt.get_version("v1") is not None
    (root / "v1" / "model.zip").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="mismatch"):
        rebuilt.get_active()


def test_model_registry_rejects_unsafe_versions_and_duplicate_writes(tmp_path: Path) -> None:
    source = tmp_path / "model.zip"
    source.write_bytes(b"model")
    registry = ModelRegistry(tmp_path / "registry")
    with pytest.raises(ValueError, match="safe"):
        registry.register("../escape", source, {}, {})
    registry.register("v1", source, {}, {})
    with pytest.raises(ValueError, match="already exists"):
        registry.register("v1", source, {}, {})


def test_corrupt_circuit_breaker_state_fails_closed(tmp_path: Path) -> None:
    state = tmp_path / "breaker.json"
    state.write_text("{broken")
    breaker = CircuitBreaker(BreakerConfig(state_file=state))
    assert breaker.state is BreakerState.TRIPPED
    assert breaker.persistence_degraded is True
    assert breaker.trip_reason and breaker.trip_reason.startswith("persisted_state_invalid")


def test_governance_outbox_recovers_exactly_once(tmp_path: Path) -> None:
    ledger = FlakyLedger(tmp_path / "audit.jsonl")
    service = GovernanceService(tmp_path / "proposals.json", ledger)
    request = ProposalCreate(
        operation="risk.pause",
        summary="Pause risk",
        payload={},
        risk_tier=RiskTier.HIGH,
        expires_in_seconds=900,
        idempotency_key="governance-outbox-001",
    )
    with pytest.raises(GovernanceAuditPending):
        service.create(request, actor="alice")
    stored = service.list()
    assert len(stored) == 1
    assert service.audit_health(reconcile=False)["pending"] == 1

    ledger.fail = False
    result = service.reconcile_audit()
    assert result["delivered"] == 1
    assert result["pending"] == 0
    assert ledger.verify()["entries"] == 1
    assert service.create(request, actor="alice").proposal_id == stored[0].proposal_id
    assert ledger.verify()["entries"] == 1


def test_critical_governance_requires_four_eyes(tmp_path: Path) -> None:
    service = GovernanceService(
        tmp_path / "proposals.json",
        AuditLedger(tmp_path / "audit.jsonl", hmac_key="x" * 32),
    )
    proposal = service.create(
        ProposalCreate(
            operation="trading.start_live",
            summary="Start live",
            payload={"model_version": "v1"},
            risk_tier=RiskTier.CRITICAL,
            expires_in_seconds=900,
            idempotency_key="critical-four-eyes-001",
        ),
        actor="alice",
    )
    with pytest.raises(GovernanceConflict, match="different from its creator"):
        service.decide(
            proposal.proposal_id,
            DecisionRequest(
                expected_revision=proposal.revision,
                actor="alice",
                decision="approve",
                reason="self approval",
            ),
        )


def test_trigger_outbox_recovers_without_losing_created_trigger(tmp_path: Path) -> None:
    ledger = FlakyLedger(tmp_path / "audit.jsonl")
    repository = TriggerRepository(tmp_path / "triggers.json", ledger=ledger)
    request = TriggerCreate(
        trigger_id="daily_review",
        kind=TriggerKind.INTERVAL,
        interval_seconds=3600,
        operation="research.refresh",
        summary="Refresh research",
    )
    with pytest.raises(GovernanceAuditPending):
        repository.create(request, actor="alice")
    assert repository.get("daily_review").trigger_id == "daily_review"
    ledger.fail = False
    result = repository.reconcile_audit()
    assert result["delivered"] == 1
    assert repository.create(request, actor="alice").trigger_id == "daily_review"
    assert ledger.verify()["entries"] == 1


def test_websocket_protocol_and_log_cursor_are_bounded() -> None:
    import base64

    token = base64.urlsafe_b64encode(b"secret-key").decode().rstrip("=")
    decoded, selected = decode_websocket_auth_protocol(
        f"trading-gym.v1, auth.{token}"
    )
    assert decoded == "secret-key"
    assert selected == "trading-gym.v1"

    buffer = BoundedLogBuffer(maxlen=2)
    for value in range(4):
        buffer.append(level="INFO", source="test", message=str(value))
    gap, records = buffer.after(1)
    assert gap is True
    assert [item["sequence"] for item in records] == [3, 4]


def test_child_environment_does_not_leak_control_plane_secrets(monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "api")
    monkeypatch.setenv("WORKER_API_KEY", "worker")
    monkeypatch.setenv("APPROVAL_API_KEY", "approval")
    monkeypatch.setenv("AUDIT_HMAC_KEY", "audit")
    monkeypatch.setenv("ACTOR_CREDENTIALS_JSON", '{"alice":{"secret":"principal-secret-000000000000000001","roles":["proposer"]}}')
    monkeypatch.setenv("ABAN_API_KEY", "broker")
    monkeypatch.setenv("GITHUB_TOKEN", "github")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    monkeypatch.setenv("SAFE_VALUE", "visible")
    environment = child_environment()
    assert environment["SAFE_VALUE"] == "visible"
    assert environment["TOKENIZERS_PARALLELISM"] == "false"
    assert not (
        {
            "API_KEY",
            "WORKER_API_KEY",
            "APPROVAL_API_KEY",
            "AUDIT_HMAC_KEY",
            "AUDIT_HMAC_KEYRING_JSON",
            "ACTOR_CREDENTIALS_JSON",
            "ABAN_API_KEY",
            "GITHUB_TOKEN",
        }
        & environment.keys()
    )

    delegated = child_environment(allowed_secret_env={"ABAN_API_KEY"})
    assert delegated["ABAN_API_KEY"] == "broker"
    assert "GITHUB_TOKEN" not in delegated


@pytest.mark.asyncio
async def test_panic_signal_persists_until_flatten_succeeds(tmp_path: Path) -> None:
    class Adapter:
        def __init__(self, outcomes: list[bool]) -> None:
            self.outcomes = outcomes

        async def flatten(self) -> bool:
            return self.outcomes.pop(0)

    panic = tmp_path / "panic.signal"
    panic.write_text("panic\n", encoding="utf-8")
    adapter = Adapter([False, True])

    assert await handle_panic_request(adapter, panic) is False
    assert panic_requested(panic) is True
    assert await handle_panic_request(adapter, panic) is True
    assert panic_requested(panic) is False


@pytest.mark.asyncio
async def test_symlink_panic_is_fail_closed_and_not_cleared(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.write_text("panic\n", encoding="utf-8")
    panic = tmp_path / "panic.signal"
    panic.symlink_to(target)

    class Adapter:
        async def flatten(self) -> bool:
            return True

    assert panic_requested(panic) is True
    with pytest.raises(ValueError, match="invalid"):
        await handle_panic_request(Adapter(), panic)
    assert panic.is_symlink()


def test_process_identity_prevents_pid_only_signalling() -> None:
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    process = subprocess.Popen(command)
    try:
        identity = capture_process_identity(process.pid, command)
        assert process_identity_matches(identity) is True
        forged = {**identity, "command_sha256": "0" * 64}
        assert process_identity_matches(forged) is False
        with pytest.raises(ProcessLookupError):
            terminate_process_tree(forged, graceful_timeout=0.1)
        result = terminate_process_tree(identity, graceful_timeout=1.0)
        assert result["remaining"] == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_training_api_uses_valid_cli_shape(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "training_t4.yaml").write_text("{}\n", encoding="utf-8")
    captured: dict[str, object] = {}

    async def fake_start(process_key, command, **kwargs):  # type: ignore[no-untyped-def]
        captured["process_key"] = process_key
        captured["command"] = command
        captured["kwargs"] = kwargs
        return {"pid": 1234}

    monkeypatch.setattr(api_main, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(api_main, "start_managed_process", fake_start)

    result = asyncio.run(
        api_main.start_training(
            api_main.TrainingStartRequest(
                config_name="training_t4.yaml",
                model_version="candidate-v1",
            ),
            api_key="test",
        )
    )
    command = captured["command"]
    assert isinstance(command, list)
    assert command[:3] == [sys.executable, "-m", "apps.trainer.main"]
    assert "train" not in command
    assert result == {
        "status": "started",
        "pid": 1234,
        "config": "training_t4.yaml",
        "model_version": "candidate-v1",
    }


def test_emergency_kill_reports_flatten_evidence_without_overclaiming(
    tmp_path: Path, monkeypatch
) -> None:
    panic = tmp_path / "panic.signal"
    monkeypatch.setattr(api_main, "PANIC_FILE", panic)
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AUDIT_HMAC_KEY", "e" * 32)
    monkeypatch.setenv("EMERGENCY_API_KEY", "emergency-secret")
    monkeypatch.setenv("EMERGENCY_FLATTEN_GRACE_SECONDS", "1")
    api_main.PROCESSES.clear()
    api_main.PROCESSES["trading"] = {"status": "running", "pid": 123}

    async def fake_wait(process_key: str, *, timeout: float) -> bool:
        assert process_key == "trading"
        assert timeout == 1.0
        panic.unlink()
        return True

    async def fake_cleanup() -> None:
        api_main.PROCESSES.clear()

    monkeypatch.setattr(api_main, "wait_for_managed_process_exit", fake_wait)
    monkeypatch.setattr(api_main, "cleanup_processes", fake_cleanup)
    result = asyncio.run(
        api_main.emergency_kill_all(
            api_main.EmergencyKillRequest(confirmation="CONFIRM_KILL"),
            api_key="test",
            actor="operator",
            emergency_key="emergency-secret",
        )
    )
    assert result["status"] == "emergency_killed"
    assert result["trading_was_active"] is True
    assert result["flatten_confirmed"] is True
    assert result["panic_signal_retained"] is False


def test_paper_start_uses_live_state_confirmation_and_no_order_worker(
    tmp_path: Path, monkeypatch
) -> None:
    checkpoints = tmp_path / "checkpoints"
    source = tmp_path / "best_model.zip"
    source.write_bytes(b"model")
    ModelRegistry(checkpoints).register("v1", source, {}, {}, set_active=False)
    captured: dict[str, object] = {}

    async def fake_start(process_key, command, **kwargs):  # type: ignore[no-untyped-def]
        captured["process_key"] = process_key
        captured["command"] = command
        captured["kwargs"] = kwargs
        return {"pid": 4321}

    monkeypatch.setattr(api_main, "CHECKPOINTS_DIR", checkpoints)
    monkeypatch.setattr(api_main, "start_managed_process", fake_start)
    result = asyncio.run(
        api_main.start_trading(
            api_main.TradingStartRequest(mode="paper", model_version="v1"),
            api_key="test",
            actor="operator",
            approval_key=None,
        )
    )
    command = captured["command"]
    assert isinstance(command, list)
    assert command[:4] == [sys.executable, "-m", "apps.paper.main", "run"]
    assert "--live" in command
    assert "--model-path" in command
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["allowed_secret_env"] == {"ABAN_API_KEY"}
    assert result["mode"] == "paper"
