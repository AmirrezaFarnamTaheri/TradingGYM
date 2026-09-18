from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from axiomforge.live_trading import LiveConsent
from trading_gym.production.emergency import EmergencyController
from trading_gym.production.runtime import LiveExecutionCoordinator
from trading_gym.production.venues import VenueOrderIntent


class FakeVenue:
    def __init__(self):
        self.calls = 0

    def submit(self, intent):
        self.calls += 1
        return {"ok": True, "operation": intent.operation}


def consent(*, actor: str = "executor", seconds: int = 60) -> LiveConsent:
    return LiveConsent(
        actor=actor,
        scope="live.execute",
        expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=seconds)
        ).isoformat(),
        nonce="nonce",
    )


def intent(key: str = "idem-1") -> VenueOrderIntent:
    return VenueOrderIntent(
        venue="abantether",
        operation="spot.market",
        base_symbol="BTC",
        quote_symbol="USDT",
        side="buy",
        quantity=Decimal("0.01"),
        idempotency_key=key,
    )


def test_live_consent_binds_each_mutation_and_deduplicates(tmp_path) -> None:
    venue = FakeVenue()
    coordinator = LiveExecutionCoordinator(
        {"abantether": venue},
        emergency_controller=EmergencyController(tmp_path / "emergency.json"),
    )
    c = consent()
    i = intent()
    auth = coordinator.bind_authorization(i, consent=c, proposal_id="proposal")
    first = coordinator.execute(i, auth, consent=c)
    second = coordinator.execute(i, auth, consent=c)
    assert first == second
    assert venue.calls == 1


def test_live_coordinator_rejects_tamper_expiry_and_emergency_halt(tmp_path) -> None:
    venue = FakeVenue()
    emergency = EmergencyController(tmp_path / "emergency.json")
    coordinator = LiveExecutionCoordinator(
        {"abantether": venue}, emergency_controller=emergency
    )
    c = consent()
    i = intent()
    auth = coordinator.bind_authorization(i, consent=c, proposal_id="proposal")
    tampered = VenueOrderIntent(**{**i.__dict__, "quantity": Decimal("0.02")})
    with pytest.raises(PermissionError, match="authorization_mismatch"):
        coordinator.execute(tampered, auth, consent=c)
    with pytest.raises(PermissionError):
        coordinator.bind_authorization(
            i, consent=consent(seconds=-1), proposal_id="proposal"
        )
    emergency.transition(
        halt=True,
        reason="operator halt",
        expected_revision=0,
        actor="operator",
        approver="supervisor",
    )
    with pytest.raises(PermissionError, match="emergency_halt"):
        coordinator.execute(i, auth, consent=c)
