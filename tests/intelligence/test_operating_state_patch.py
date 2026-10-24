from dataclasses import replace

import numpy as np
import pytest

from trading_gym.common.intelligence_config import IntelligenceConfig, IntelligenceMode
from trading_gym.intelligence.factory import build_intelligence_policy
from trading_gym.intelligence.fusion_backend import FusionBackend
from trading_gym.intelligence.operating_state import (
    OperatingPolicy,
    OperatingSnapshot,
    OperatingStatus,
    admit_target,
    fallback_guard,
)
from trading_gym.intelligence.policy_adapter import IntelligencePolicyAdapter
from trading_gym.intelligence.types import (
    ActionKind,
    BackendUnavailable,
    DecisionSignal,
    MarketContext,
    UncertainDecision,
)


def signal(value=1.0):
    kind = (
        ActionKind.HOLD
        if value == 0
        else (ActionKind.LONG if value > 0 else ActionKind.SHORT)
    )
    return DecisionSignal(
        "local", kind, value, 0.7, "bounded candidate", source_id="local/v1"
    )


SNAPSHOT = OperatingSnapshot(
    "paper-A", "paper", 100, 100, 100, 1, 100, 100, True, last_accepted_target_units=1
)
POLICY = OperatingPolicy("declared-local", "paper-A", "paper", 1.5, 3)
CONTEXT = MarketContext("BTCUSDT", 1, 100, None, 100, 99)


def test_smaller_cap_does_not_authorize_larger_realized_target():
    # Old accepted raw .1 with cap3 is .1; new raw2 with cap1.5 is 1.5.
    state = replace(SNAPSHOT, position_units=0.1, last_accepted_target_units=0.1)
    denied = admit_target(signal(2), state, POLICY)
    assert denied.status is OperatingStatus.OBSERVE
    assert denied.reason == "risk_increase_not_authorized"
    assert denied.signal is None and denied.target_units is None
    assert admit_target(signal(0.05), state, POLICY).target_units == 0.05


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"orders_known": False}, OperatingStatus.RECONCILE),
        ({"pending_buy_units": 2, "pending_sell_units": 2}, OperatingStatus.RECONCILE),
        ({"account_observed_at": 90}, OperatingStatus.RECONCILE),
        ({"market_observed_at": 90}, OperatingStatus.OBSERVE),
        ({"halted": True}, OperatingStatus.STOPPED),
    ],
)
def test_unknown_pending_stale_and_halted_are_not_trade_targets(changes, expected):
    result = admit_target(signal(0.5), replace(SNAPSHOT, **changes), POLICY)
    assert result.status is expected and result.signal is None
    assert result.account_id == "paper-A" and result.mode == "paper"


def test_permissions_are_separate_from_hardware_and_account_identity():
    flat = replace(
        SNAPSHOT,
        position_units=0,
        last_accepted_target_units=0,
        capabilities=(("gpu", True), ("api", True)),
    )
    assert admit_target(signal(1), flat, POLICY).signal is None
    explicit = replace(POLICY, allow_open_from_flat=True, allow_risk_increase=True)
    assert admit_target(signal(1), flat, explicit).target_units == 1
    required = replace(POLICY, required_capabilities=("governor",))
    assert (
        admit_target(signal(0.5), SNAPSHOT, required).reason
        == "required_capability_unavailable"
    )
    with pytest.raises(PermissionError, match="account/mode"):
        admit_target(signal(0.5), replace(SNAPSHOT, mode="live"), POLICY)
    assert (
        admit_target(signal(-0.5), SNAPSHOT, POLICY).reason
        == "direction_change_not_authorized"
    )
    assert admit_target(signal(0), SNAPSHOT, POLICY).target_units == 0


class Fixed:
    def __init__(self, value, name):
        self.value, self.name = value, name

    def is_available(self):
        return True

    def decide(self, context):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_fallback_requires_explicit_account_guard_and_preserves_source():
    first = Fixed(BackendUnavailable("down"), "primary")
    second = Fixed(signal(0.5), "local")
    kwargs = dict(
        action_low=-5,
        action_high=5,
        fallback_backend=second,
        context_builder=lambda _: CONTEXT.__dict__,
    )
    adapter = IntelligencePolicyAdapter(first, **kwargs)
    with pytest.raises(UncertainDecision, match="operating-state admission"):
        adapter.predict(np.zeros(6))
    assert adapter.last_signal is None
    adapter = IntelligencePolicyAdapter(
        first, fallback_admission=fallback_guard(lambda: SNAPSHOT, POLICY), **kwargs
    )
    action, _ = adapter.predict(np.zeros(6))
    assert action[0] == 0.5 and adapter.last_signal.source_id == "local/v1"
    assert adapter.last_signal.backend == "local(degraded)"
    mismatch = replace(SNAPSHOT, price=101)
    with pytest.raises(UncertainDecision, match="snapshot mismatch"):
        fallback_guard(lambda: mismatch, POLICY)(signal(0.5), CONTEXT)


def test_governor_outage_cannot_silently_restore_raw_rl():
    fusion = FusionBackend(
        rl_backend=Fixed(signal(4), "rl"),
        llm_backend=Fixed(BackendUnavailable("down"), "llm"),
    )
    with pytest.raises(BackendUnavailable, match="down"):
        fusion.decide(CONTEXT)


def test_fallback_guard_cannot_rewrite_composite_contributors():
    first = Fixed(BackendUnavailable("down"), "primary")
    candidate = replace(
        signal(0.5), contributing_sources=("rl:model-a", "governor:model-b")
    )
    second = Fixed(candidate, "local")
    adapter = IntelligencePolicyAdapter(
        first,
        action_low=-5,
        action_high=5,
        fallback_backend=second,
        context_builder=lambda _: CONTEXT.__dict__,
        fallback_admission=lambda signal, context: replace(
            signal, contributing_sources=("rl:model-a", "governor:wrong-model")
        ),
    )
    with pytest.raises(PermissionError, match="evidence provenance"):
        adapter.predict(np.zeros(6))
    assert adapter.last_signal is None


def test_configured_missing_provider_is_not_a_local_profile():
    class Repo:
        def list(self):
            return []

    with pytest.raises(ValueError, match="not found/enabled"):
        build_intelligence_policy(
            config=IntelligenceConfig(
                mode=IntelligenceMode.AUTO, llm_provider_id="missing"
            ),
            model=None,
            action_low=-5,
            action_high=5,
            provider_repository=Repo(),
        )
    local = build_intelligence_policy(
        config=IntelligenceConfig(mode=IntelligenceMode.RULES),
        model=None,
        action_low=-5,
        action_high=5,
        provider_repository=Repo(),
    )
    assert local.backend.name == "rules"
