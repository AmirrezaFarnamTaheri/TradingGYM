from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.execution.live_trading import LiveConsent
from trading_gym.execution.production.emergency import EmergencyController
from trading_gym.execution.production.runtime import LiveExecutionCoordinator
from trading_gym.execution.production.venues import VenueOrderIntent


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


@pytest.mark.asyncio
async def test_model_free_system1_runs_only_in_non_mutating_live_dry_run(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    import numpy as np
    import pandas as pd

    from trading_gym.execution.live import main as live
    from trading_gym.core.config import load_app_config
    from trading_gym.core.intelligence_config import IntelligenceConfig

    cfg = load_app_config("config/app.yaml")
    cfg.intelligence = IntelligenceConfig(
        mode="system1", system1_provider_ids=("fast-one",)
    )
    features = pd.DataFrame(
        {"spread_ratio": [0.0002, 0.0002], "volatility": [0.001, 0.001]},
        index=pd.date_range("2025-01-01", periods=2, freq="5min", tz="UTC"),
    )
    prices = np.array([100.0, 101.0])
    inputs = {
        "features": features,
        "prices_mid": prices,
        "prices_high": prices,
        "prices_low": prices,
    }
    policy = SimpleNamespace(backend=SimpleNamespace(name="test-system1"))
    calls: list[object] = []

    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            self.api = object()

    monkeypatch.setattr(live, "DryRunLiveExecutionAdapter", FakeAdapter)
    monkeypatch.setattr(live, "load_environment_inputs", lambda cfg, timeframe: inputs)

    def build_policy(cfg, model, env):
        assert model is None
        assert env.live_mode and env.model_bundle_id == "no-model"
        return policy

    monkeypatch.setattr(live, "build_trading_policy", build_policy)

    async def trade(**kwargs):
        calls.append(kwargs["model"])

    monkeypatch.setattr(live, "trade_loop", trade)
    monkeypatch.setattr(
        live.PPO,
        "load",
        lambda snapshot: pytest.fail("System-1 dry-run loaded a model"),
    )
    await live.run_live_trading(cfg, None, None, "fake-token", True)
    assert calls == [policy]


@pytest.mark.asyncio
async def test_unqualified_system1_never_enters_funded_live_execution(
    monkeypatch,
) -> None:
    from types import SimpleNamespace
    from trading_gym.execution.live import main as live
    from trading_gym.core.intelligence_config import IntelligenceConfig

    cfg = SimpleNamespace(
        intelligence=IntelligenceConfig(
            mode="system1",
            system1_provider_ids=("fast-one",),
        )
    )
    monkeypatch.setattr(
        live.PPO,
        "load",
        lambda snapshot: pytest.fail("unqualified System-1 loaded a model"),
    )
    with pytest.raises(PermissionError, match="unqualified_live_intelligence"):
        await live.run_live_trading(
            cfg,
            None,
            None,
            "fake-token",
            False,
        )
