"""ADV evidence does not mutate portfolio plans or authorize execution."""

from decimal import Decimal

import pytest

from trading_gym.execution.portfolio import AdvSnapshot, assess_rebalance_capacity
from trading_gym.execution.portfolio.rebalance import (
    RebalanceHolding,
    RebalanceTarget,
    plan_rebalance,
)


def _plan():
    return plan_rebalance(
        base_currency="USD",
        cash_base=Decimal("10000"),
        holdings=[
            RebalanceHolding(
                instrument_id="BTCUSDT",
                quantity=Decimal("0"),
                price_base=Decimal("100"),
                quantity_increment=Decimal("1"),
            ),
        ],
        targets=[
            RebalanceTarget(instrument_id="BTCUSDT", target_weight=Decimal("0.1")),
        ],
    )


def _evidence(
    amount="40000",
    observed=1_700_000_000,
    source="b" * 64,
    currency="USD",
):
    return AdvSnapshot(
        instrument_id="BTCUSDT",
        traded_value_base=Decimal(amount),
        observed_at_unix=observed,
        source_sha256=source,
        base_currency=currency,
    )


def test_real_rebalance_plan_is_checked_without_mutating_existing_cash_or_trades():
    plan = _plan()
    assert plan.feasible and len(plan.trades) == 1
    before = plan.model_dump(mode="json")
    report = assess_rebalance_capacity(
        plan=plan, observations=[_evidence()], as_of_unix=1_700_000_100
    )
    assert report["feasible"] is True
    assert report["advisory_only"] is True
    assert report["execution_authority_granted"] is False
    assert report["plan_sha256"] == plan.plan_sha256
    assert report["items"][0]["participation"] == "0.025"
    assert report["total_capacity_shortfall_base"] == "0"
    assert plan.model_dump(mode="json") == before
    assert len(report["report_sha256"]) == 64


def test_missing_adv_and_capacity_excess_are_explicit_blockers():
    plan = _plan()
    for observations, prefix in (
        ([], "adv_missing"),
        ([_evidence(amount="10000")], "capacity_exceeded"),
        ([_evidence(amount="0")], "adv_zero"),
        ([_evidence(observed=1_700_000_500)], "adv_future"),
        ([_evidence(observed=1_600_000_000)], "adv_stale"),
    ):
        result = assess_rebalance_capacity(
            plan=plan, observations=observations, as_of_unix=1_700_000_100
        )
        assert result["feasible"] is False
        assert f"{prefix}:BTCUSDT" in result["blockers"]
    result = assess_rebalance_capacity(
        plan=plan,
        observations=[_evidence(amount="10000")],
        as_of_unix=1_700_000_100,
    )
    assert result["items"][0]["shortfall_base"] == "500.00"


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1"])
def test_malformed_adv_values_are_invalid_evidence(amount):
    with pytest.raises(ValueError):
        assess_rebalance_capacity(
            plan=_plan(),
            observations=[_evidence(amount=amount)],
            as_of_unix=1_700_000_100,
        )


def test_currency_mismatch_and_unverified_hash_fail_closed():
    for evidence in (
        _evidence(currency="EUR"),
        _evidence(source="not-a-digest"),
    ):
        with pytest.raises(ValueError):
            assess_rebalance_capacity(
                plan=_plan(),
                observations=[evidence],
                as_of_unix=1_700_000_100,
            )


def test_duplicate_sources_and_invalid_policy_limit_fail_closed():
    plan = _plan()
    with pytest.raises(ValueError):
        assess_rebalance_capacity(
            plan=plan,
            observations=[_evidence(), _evidence()],
            as_of_unix=1_700_000_100,
        )
    for bad in (Decimal("-0.1"), Decimal("1.1"), Decimal("NaN"), True):
        with pytest.raises(ValueError):
            assess_rebalance_capacity(
                plan=plan,
                observations=[_evidence()],
                as_of_unix=1_700_000_100,
                max_participation=bad,
            )


def test_receipts_are_deterministic_and_source_bound():
    plan = _plan()
    first = assess_rebalance_capacity(
        plan=plan,
        observations=[_evidence()],
        as_of_unix=1_700_000_100,
    )
    second = assess_rebalance_capacity(
        plan=plan,
        observations=[_evidence()],
        as_of_unix=1_700_000_100,
    )
    third = assess_rebalance_capacity(
        plan=plan,
        observations=[_evidence(source="c" * 64)],
        as_of_unix=1_700_000_100,
    )
    assert first == second
    assert first["report_sha256"] != third["report_sha256"]
