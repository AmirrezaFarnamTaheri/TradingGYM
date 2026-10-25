from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from trading_gym.core.legacy_api import main as api_main
from trading_gym.execution.governance.ledger import AuditLedger
from trading_gym.execution.governance.models import (
    DecisionRequest,
    ProposalCreate,
    ProposalState,
    ReservationReleaseRequest,
    ReservationRequest,
    RiskTier,
)
from trading_gym.execution.governance.service import GovernanceConflict, GovernanceService
from trading_gym.rl.models.versioning import ModelRegistry


def _approved(service: GovernanceService):
    proposal = service.create(
        ProposalCreate(
            operation="trading.start_live",
            summary="Start governed live runtime",
            payload={"model_version": "v1"},
            risk_tier=RiskTier.CRITICAL,
            expires_in_seconds=900,
            idempotency_key="reservation-lifecycle-001",
        ),
        actor="creator",
    )
    return service.decide(
        proposal.proposal_id,
        DecisionRequest(
            expected_revision=proposal.revision,
            actor="reviewer",
            decision="approve",
            reason="reviewed",
        ),
    )


def test_reservation_prevents_double_consume_and_can_be_released(
    tmp_path: Path,
) -> None:
    service = GovernanceService(
        tmp_path / "proposals.json",
        AuditLedger(tmp_path / "audit.jsonl", hmac_key="x" * 32),
    )
    approved = _approved(service)
    reservation = ReservationRequest(
        expected_revision=approved.revision,
        actor="runtime",
        reservation_id="a" * 32,
    )
    reserved = service.reserve(approved.proposal_id, reservation)
    assert reserved.state is ProposalState.RESERVED

    with pytest.raises(GovernanceConflict, match="not approved|expected revision"):
        service.reserve(
            approved.proposal_id,
            ReservationRequest(
                expected_revision=approved.revision,
                actor="other-runtime",
                reservation_id="b" * 32,
            ),
        )

    released = service.release_reservation(
        approved.proposal_id,
        ReservationReleaseRequest(
            expected_revision=reserved.revision,
            actor="runtime",
            reservation_id="a" * 32,
            reason="spawn failed before side effect",
        ),
    )
    assert released.state is ProposalState.APPROVED
    assert released.reservation_id is None

    reserved_again = service.reserve(
        approved.proposal_id,
        ReservationRequest(
            expected_revision=released.revision,
            actor="runtime",
            reservation_id="c" * 32,
        ),
    )
    consumed = service.commit_reservation(
        approved.proposal_id,
        ReservationRequest(
            expected_revision=reserved_again.revision,
            actor="runtime",
            reservation_id="c" * 32,
        ),
        details={"pid": 123},
    )
    assert consumed.state is ProposalState.CONSUMED
    assert service.ledger.verify()["valid"] is True


def test_live_start_releases_reservation_when_spawn_fails(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TRADING_GYM_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AUDIT_HMAC_KEY", "h" * 32)
    monkeypatch.setenv("APPROVAL_API_KEY", "approval-secret")
    monkeypatch.setenv(
        "ACTOR_CREDENTIALS_JSON",
        json.dumps(
            {
                "runtime": {
                    "secret": "runtime-executor-secret-000000000001",
                    "roles": ["executor"],
                }
            }
        ),
    )
    service = GovernanceService()
    approved = _approved(service)
    checkpoints = tmp_path / "checkpoints"
    source = tmp_path / "best_model.zip"
    source.write_bytes(b"model")
    ModelRegistry(checkpoints).register("v1", source, {}, {})
    monkeypatch.setattr(api_main, "CHECKPOINTS_DIR", checkpoints)

    async def failing_start(*args, **kwargs):  # type: ignore[no-untyped-def]
        before_spawn = kwargs.get("before_spawn")
        assert before_spawn is not None
        before_spawn()
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(api_main, "start_managed_process", failing_start)
    request = api_main.TradingStartRequest(
        mode="live",
        model_version="v1",
        proposal_id=approved.proposal_id,
        expected_revision=approved.revision,
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            api_main.start_trading(
                request,
                api_key="test",
                actor="runtime",
                actor_key="runtime-executor-secret-000000000001",
                approval_key="approval-secret",
            )
        )
    assert error.value.status_code == 500
    latest = service.get(approved.proposal_id)
    assert latest.state is ProposalState.APPROVED
    assert latest.reservation_id is None
