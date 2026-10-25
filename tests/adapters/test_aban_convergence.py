from __future__ import annotations

import json
from pathlib import Path

from trading_gym.market.adapters import AbanTransport, EnhancedAbanClient, RateLimitedAbanSDK
from trading_gym.market.venue import asset_registry


def test_aban_policy_is_single_short_rule_authority() -> None:
    root = Path(__file__).resolve().parents[2]
    policy = json.loads(
        (root / "trading_gym/market/adapters/aban_tether_policy.json").read_text()
    )
    assert asset_registry.SHORTABLE_ASSETS == frozenset(policy["shortable"])
    assert asset_registry.SHORT_BORROW_CAPS == {
        key: float(value) for key, value in policy["short_borrow_caps"].items()
    }
    assert asset_registry.get_max_leverage("BTCUSDT", is_short=True) == float(
        policy["max_short_leverage"]
    )
    assert asset_registry.get_max_leverage("SOLUSDT", is_short=True) == 0.0


def test_adapter_public_facade_and_transport_have_one_dispatch_layer() -> None:
    assert EnhancedAbanClient is not None
    assert RateLimitedAbanSDK is not None
    assert AbanTransport is not None
    transport = object.__new__(AbanTransport)
    assert not hasattr(transport, "_last")
    assert not hasattr(transport, "_acquire")
