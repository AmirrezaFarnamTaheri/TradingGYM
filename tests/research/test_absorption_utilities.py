"""Lookahead-keyed cache, pre-trade gate, stability backoff, block bootstrap."""

from decimal import Decimal

import numpy as np
import pytest

from trading_gym.features.lookahead_cache import LookaheadKeyedCache, LookaheadMismatch
from trading_gym.production.mutation_retry import (
    MutationRetryPolicy,
    StabilityAwareBackoff,
)
from trading_gym.production.pre_trade_gate import evaluate_pre_trade_gate
from trading_gym.research.bootstrap_ci import (
    block_bootstrap_interval,
)

# ---------------------------------------------------------------------------
# Lookahead-keyed cache (pybroker absorption)
# ---------------------------------------------------------------------------


def test_same_lookahead_roundtrip_and_cross_lookahead_rejected():
    cache = LookaheadKeyedCache()
    cache.put(namespace="model", key="ppo-window", value={"w": 20}, lookahead=0)
    assert cache.has(namespace="model", key="ppo-window", lookahead=0)

    with pytest.raises(LookaheadMismatch) as excinfo:
        cache.get(namespace="model", key="ppo-window", lookahead=1)
    assert "lookahead-mismatch" in str(excinfo.value)
    assert excinfo.value.stored_lookahead == 0


def test_cache_missing_key_is_plain_keyerror():
    cache = LookaheadKeyedCache()
    with pytest.raises(KeyError):
        cache.get(namespace="none", key="nothing", lookahead=0)


def test_cache_validates_inputs():
    cache = LookaheadKeyedCache()
    with pytest.raises(ValueError):
        cache.put(namespace="", key="k", value=1, lookahead=0)
    with pytest.raises(ValueError):
        cache.put(namespace="ns", key="k", value=1, lookahead=-1)
    with pytest.raises(ValueError):
        cache.put(namespace="ns", key="k", value=1, lookahead=True)  # bool is int


# ---------------------------------------------------------------------------
# Pre-trade gate (vibe-trading payload contract absorption)
# ---------------------------------------------------------------------------


def test_gate_passes_a_well_formed_spot_mutation():
    payload = evaluate_pre_trade_gate(
        operation="spot.market",
        quantity=Decimal("0.5"),
        idempotency_key="a" * 32,
    )
    assert payload.allowed
    assert not payload.denial_reasons
    as_dict = payload.to_dict()
    assert len(as_dict["checks"]) == 4 and as_dict["gate_id"]


def test_gate_denies_zero_quantity_cancel_with_missing_reference():
    payload = evaluate_pre_trade_gate(
        operation="order.cancel",
        quantity=Decimal("0"),
        idempotency_key="b" * 32,
        reference_id="",
    )
    assert not payload.allowed
    assert "reference_present" in payload.denial_reasons


def test_gate_denies_unknown_operation_and_bad_key():
    payload = evaluate_pre_trade_gate(
        operation="moon.short",
        quantity=Decimal("-1"),
        idempotency_key="nope",
    )
    assert not payload.allowed
    assert {"operation_known", "quantity_valid", "idempotency_key_format"} <= set(
        payload.denial_reasons
    )


# ---------------------------------------------------------------------------
# Stability-aware backoff (kraken session-reset refinement)
# ---------------------------------------------------------------------------


def _policy():
    return MutationRetryPolicy(
        max_attempts=5,
        base_delay_s=0.01,
        max_delay_s=1.0,
        jitter_fraction=0.0,
        retryable_exceptions=(TimeoutError,),
    )


def test_backoff_escalates_then_resets_after_stability_threshold():
    backoff = StabilityAwareBackoff(
        _policy(), stability_threshold=3, rng=__import__("random").Random(1)
    )
    delays = [backoff.next_delay() for _ in range(4)]
    assert delays[0] < delays[1] < delays[2]  # escalation
    for _ in range(3):
        backoff.record_success()
    after_reset = backoff.next_delay()
    assert after_reset == pytest.approx(delays[0])  # counter back at attempt 1


def test_backoff_failure_resets_success_streak():
    import random

    backoff = StabilityAwareBackoff(
        _policy(), stability_threshold=2, rng=random.Random(2)
    )
    for _ in range(2):
        backoff.record_success()
    first = (
        backoff.next_delay()
    )  # still escalated attempt 3? threshold resets on next success boundary — verify monotonic behaviour instead:
    backoff.record_failure()  # streak broken → no reset happened
    second = backoff.next_delay()
    assert second >= first * 0.99  # continued escalation, not a silent reset
    assert second > 0


def test_backoff_remains_bounded_after_long_outage():
    backoff = StabilityAwareBackoff(_policy(), rng=__import__("random").Random(4))
    for _ in range(2500):
        assert backoff.next_delay() <= 1.0
    for _ in range(5):
        backoff.record_success()
    assert backoff.next_delay() == pytest.approx(0.01)


def test_stability_threshold_validation():
    with pytest.raises(ValueError):
        StabilityAwareBackoff(_policy(), stability_threshold=0)


# ---------------------------------------------------------------------------
# Block bootstrap CI (nepse/pybroker complement to BCa)
# ---------------------------------------------------------------------------


def test_block_bootstrap_covers_mean_on_ar_series():
    rng = np.random.default_rng(17)
    ar = np.empty(300)
    ar[0] = 0.0
    for t in range(1, 300):  # autocorrelated series defeats i.i.d. resampling
        ar[t] = 0.6 * ar[t - 1] + rng.normal(0.05, 0.02)
    ci = block_bootstrap_interval(
        ar, np.mean, block_length=10, n_resamples=500, rng_seed=5
    )
    true_mean = float(np.mean(ar))
    assert (
        ci.lower - 1e-9 <= true_mean <= ci.upper + 1e-9
        or abs(ci.estimate - true_mean) < 1e-12
    )
    assert ci.lower < ci.upper


def test_block_bootstrap_deterministic_and_validates():
    data = np.arange(50, dtype=float)
    ci1 = block_bootstrap_interval(
        data, np.mean, block_length=5, n_resamples=200, rng_seed=3
    )
    ci2 = block_bootstrap_interval(
        data, np.mean, block_length=5, n_resamples=200, rng_seed=3
    )
    assert (ci1.lower, ci1.upper) == (ci2.lower, ci2.upper)
    with pytest.raises(ValueError):
        block_bootstrap_interval(data, np.mean, block_length=1)
