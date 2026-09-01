from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import apps.api.main as api_main
from apps.api.convergence import create_convergence_router
from trading_gym.agent.providers import ProviderConfig, ProviderKind, ProviderRepository
from trading_gym.common.audit_outbox import AuditOutboxPending
from trading_gym.experiments import ExperimentCreate, ExperimentRepository, ExperimentState
from trading_gym.governance.health import audit_outbox_health
from trading_gym.governance.ledger import AuditLedger
from trading_gym.market import (
    AssetClass,
    DatasetCatalog,
    DatasetRegistration,
    InstrumentRegistry,
    InstrumentSpec,
)
from trading_gym.research.workspace import ResearchWorkspace, WatchlistEntry
from trading_gym.strategy_lab.repository import StrategyRepository
from trading_gym.strategy_lab.templates import template_catalogue


class FlakyLedger(AuditLedger):
    def __init__(self, path: Path) -> None:
        super().__init__(path, hmac_key="z" * 32)
        self.fail = True

    def append(self, **kwargs):  # type: ignore[no-untyped-def]
        if self.fail:
            raise OSError("simulated ledger outage")
        return super().append(**kwargs)


def _dataset() -> DatasetRegistration:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return DatasetRegistration(
        instrument_id="BINANCE:BTCUSDT",
        timeframe="5m",
        start=start,
        end=start + timedelta(minutes=10),
        provider_id="fixture",
        row_count=3,
        expected_rows=3,
        completeness=1.0,
        content_sha256="a" * 64,
        storage_ref="datasets/btc.parquet",
    )


def test_all_mutable_planes_commit_audit_intent_before_delivery(tmp_path: Path) -> None:
    ledger = FlakyLedger(tmp_path / "audit.jsonl")
    repositories = [
        StrategyRepository(tmp_path / "strategies.json", ledger=ledger),
        ProviderRepository(tmp_path / "providers.json", ledger=ledger),
        InstrumentRegistry(tmp_path / "instruments.json", ledger=ledger),
        DatasetCatalog(tmp_path / "datasets.json", ledger=ledger),
        ResearchWorkspace(tmp_path / "workspace.json", ledger=ledger),
        ExperimentRepository(tmp_path / "experiments.json", ledger=ledger),
    ]

    with pytest.raises(AuditOutboxPending):
        repositories[0].save(template_catalogue()["sma_crossover"], actor="alice")
    with pytest.raises(AuditOutboxPending):
        repositories[1].save(
            ProviderConfig(
                provider_id="local_model",
                kind=ProviderKind.LOCAL,
                display_name="Local Model",
                model="fixture",
                base_url="http://127.0.0.1:9000",
            ),
            actor="alice",
        )
    with pytest.raises(AuditOutboxPending):
        repositories[2].save(
            InstrumentSpec(
                instrument_id="BINANCE:BTCUSDT",
                venue="BINANCE",
                symbol="BTCUSDT",
                asset_class=AssetClass.CRYPTO_SPOT,
                base_asset="BTC",
                quote_asset="USDT",
            ),
            actor="alice",
        )
    with pytest.raises(AuditOutboxPending):
        repositories[3].register(_dataset(), actor="alice")
    with pytest.raises(AuditOutboxPending):
        repositories[4].save_watchlist(
            WatchlistEntry(instrument_id="BINANCE:BTCUSDT"), actor="alice"
        )
    with pytest.raises(AuditOutboxPending):
        repositories[5].create(
            ExperimentCreate(
                experiment_type="research",
                name="Audit durability",
                configuration={},
                idempotency_key="audit-plane-experiment-001",
            ),
            actor="alice",
        )

    assert [repository.audit_pending() for repository in repositories] == [1] * 6
    ledger.fail = False
    assert sum(repository.reconcile_audit()["delivered"] for repository in repositories) == 6
    assert [repository.audit_pending() for repository in repositories] == [0] * 6
    assert ledger.verify()["entries"] == 6


def test_experiment_stale_reconciliation_is_audited(tmp_path: Path) -> None:
    path = tmp_path / "experiments.json"
    ledger = AuditLedger(tmp_path / "audit.jsonl", hmac_key="x" * 32)
    repository = ExperimentRepository(path, ledger=ledger)
    created = repository.create(
        ExperimentCreate(
            experiment_type="backtest",
            name="Lease audit",
            configuration={},
            idempotency_key="lease-audit-experiment-001",
            max_attempts=2,
        )
    )
    claimed = repository.claim_next(worker_id="worker-a", lease_seconds=30)
    assert claimed is not None
    document = json.loads(path.read_text())
    document["experiments"][created.experiment_id]["lease_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    path.write_text(json.dumps(document))

    reconciled = repository.get(created.experiment_id)
    assert reconciled.state is ExperimentState.STALE
    assert ledger.tail(1)[0]["event"] == "experiment.lease_stale"


def test_cross_plane_audit_health_fails_closed_on_pending_or_corrupt_store(tmp_path: Path) -> None:
    (tmp_path / "providers.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": 1,
                "providers": {},
                "audit_outbox": {"pending": {"event": "provider.saved"}},
            }
        )
    )
    pending = audit_outbox_health(tmp_path)
    assert pending["healthy"] is False
    assert pending["pending"] == 1

    (tmp_path / "providers.json").write_text("{broken")
    corrupt = audit_outbox_health(tmp_path)
    assert corrupt["healthy"] is False
    assert corrupt["degraded"] == ["providers"]


def test_high_risk_approval_blocks_when_non_governance_outbox_cannot_deliver(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path))
    monkeypatch.setenv("APPROVAL_API_KEY", "approval-secret")
    monkeypatch.setenv("AUDIT_HMAC_KEY", "a" * 32)
    monkeypatch.setenv(
        "ACTOR_CREDENTIALS_JSON",
        json.dumps(
            {
                "proposal-author": {
                    "secret": "proposal-author-secret-00000000001",
                    "roles": ["proposer"],
                },
                "reviewer": {
                    "secret": "reviewer-principal-secret-00000000001",
                    "roles": ["approver"],
                },
            }
        ),
    )
    app = FastAPI()
    app.include_router(create_convergence_router(lambda: True), prefix="/api/v1")
    client = TestClient(app)
    created = client.post(
        "/api/v1/convergence/governance/proposals",
        headers={
            "X-Actor": "proposal-author",
            "X-Actor-Key": "proposal-author-secret-00000000001",
        },
        json={
            "operation": "risk.pause",
            "summary": "Pause risk",
            "payload": {},
            "risk_tier": "high",
            "expires_in_seconds": 900,
            "idempotency_key": "cross-plane-pending-001",
        },
    ).json()
    (tmp_path / "providers.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": 0,
                "providers": {},
                "audit_outbox": {
                    "invalid": {
                        "event_id": "invalid",
                        "event": "provider.saved",
                        "actor": "",
                        "subject": "provider",
                        "details": {},
                    }
                },
            }
        )
    )
    response = client.post(
        f"/api/v1/convergence/governance/proposals/{created['proposal_id']}/decision",
        headers={
            "X-Approval-Key": "approval-secret",
            "X-Actor-Key": "reviewer-principal-secret-00000000001",
        },
        json={
            "expected_revision": created["revision"],
            "actor": "reviewer",
            "decision": "approve",
            "reason": "validated",
        },
    )
    assert response.status_code == 503


def test_model_listing_keeps_metadata_non_authoritative_and_redacted(
    tmp_path: Path, monkeypatch
) -> None:
    model_dir = tmp_path / "v1"
    model_dir.mkdir()
    (model_dir / "best_model.zip").write_bytes(b"model")
    (model_dir / "metadata.json").write_text(
        json.dumps(
            {
                "version": "forged",
                "path": "/host/secret/path",
                "has_model": False,
                "api_key": "secret",
            }
        )
    )
    monkeypatch.setattr(api_main, "CHECKPOINTS_DIR", tmp_path)
    response = asyncio.run(api_main.list_models(api_key="test"))
    model = response["models"][0]
    assert model["version"] == "v1"
    assert model["has_model"] is True
    assert "path" not in model
    assert model["metadata"]["version"] == "forged"
    assert model["metadata"]["api_key"] == "***"
