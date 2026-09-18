from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_gym.governance.ledger import AuditLedger
from trading_gym.institutional_os import (
    InstitutionalArtifactConflict,
    InstitutionalArtifactCreate,
    InstitutionalArtifactRepository,
)

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


def report_hash(report: dict) -> str:
    encoded = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def repository(tmp_path: Path) -> InstitutionalArtifactRepository:
    return InstitutionalArtifactRepository(
        path=tmp_path / "artifacts.json",
        ledger=AuditLedger(tmp_path / "audit.jsonl"),
    )


def request(**updates) -> InstitutionalArtifactCreate:
    report = {
        "market_id": "BTC-USDT",
        "blockers": [],
        "trading_authority_granted": False,
    }
    payload = {
        "domain": "market-intelligence",
        "report_sha256": report_hash(report),
        "report": report,
        "observed_at": NOW,
        "idempotency_key": "idem-market-0001",
        "artifact_id": "market-regime-btc",
        "expected_revision": 0,
        "actor": "analyst",
        "evidence_ids": ["dataset-01"],
    }
    payload.update(updates)
    return InstitutionalArtifactCreate(**payload)


def test_artifact_repository_is_cas_versioned_idempotent_and_audited(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    first = repo.save(request())
    assert first["revision"] == 1
    assert repo.audit_pending() == 0
    replay = repo.save(request())
    assert replay["version_sha256"] == first["version_sha256"]
    second = repo.save(
        request(
            expected_revision=1,
            idempotency_key="idem-market-0002",
            report_sha256=report_hash(
                {
                    "market_id": "BTC-USDT",
                    "blockers": ["stressed"],
                    "trading_authority_granted": False,
                }
            ),
            report={
                "market_id": "BTC-USDT",
                "blockers": ["stressed"],
                "trading_authority_granted": False,
            },
        )
    )
    assert second["revision"] == 2
    assert second["previous_version_sha256"] == first["version_sha256"]
    assert repo.get("market-regime-btc")["revision"] == 2
    assert repo.list(domain="market-intelligence")[0]["revision"] == 2


def test_artifact_repository_rejects_idempotency_alias_and_stale_cas(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    repo.save(request())
    with pytest.raises(InstitutionalArtifactConflict, match="aliases"):
        repo.save(
            request(
                report_sha256=report_hash({"different": True}),
                report={"different": True},
            )
        )
    with pytest.raises(InstitutionalArtifactConflict, match="expected revision"):
        repo.save(request(idempotency_key="new-idem-0001", expected_revision=0))


def test_artifact_repository_detects_payload_and_chain_tampering(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    repo.save(request())
    document = json.loads(repo.path.read_text())
    document["artifacts"]["market-regime-btc"]["versions"][0]["report"][
        "market_id"
    ] = "ETH-USDT"
    repo.path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="payload hash mismatch"):
        repo.list()


def test_artifact_repository_rejects_credentials_and_domain_changes(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="credential-like"):
        request(
            report_sha256=report_hash({"api_key": "secret"}),
            report={"api_key": "secret"},
        )
    with pytest.raises(ValueError, match="does not match"):
        request(report_sha256="a" * 64)
    repo = repository(tmp_path)
    repo.save(request())
    with pytest.raises(InstitutionalArtifactConflict, match="domain cannot change"):
        repo.save(
            request(
                domain="enterprise-risk",
                idempotency_key="new-idem-0002",
                expected_revision=1,
            )
        )


def test_auto_artifact_id_is_stable_under_idempotent_retry(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    auto = request(
        artifact_id=None,
        expected_revision=None,
        idempotency_key="auto-idempotency-0001",
    )
    first = repo.save(auto)
    second = repo.save(auto)
    assert first["artifact_id"] == second["artifact_id"]
    assert first["version_sha256"] == second["version_sha256"]
    assert len(repo.list()) == 1
