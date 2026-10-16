"""Fail-closed, point-in-time settled funding evidence across venue snapshots."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_gym.risk.funding_history import (
    FundingObservation,
    funding_cashflow_estimate,
    resolve_funding_history,
)

H = "a" * 64
T0 = datetime(2026, 1, 1, 0, tzinfo=timezone.utc)


def obs(
    effective_hours: int,
    observed_hours: int | None = None,
    rate: str = "0.001",
    sha: str = H,
):
    return FundingObservation(
        instrument_id="BTC-USDT-PERP",
        effective_at=T0 + timedelta(hours=effective_hours),
        observed_at=T0 + timedelta(
            hours=effective_hours if observed_hours is None else observed_hours
        ),
        rate=Decimal(rate),
        source_sha256=sha,
    )


def quote(data, hours, max_age=timedelta(hours=12)):
    return resolve_funding_history(
        data,
        instrument_id="BTC-USDT-PERP",
        as_of=T0 + timedelta(hours=hours),
        max_age=max_age,
    )


def test_future_only_tape_cannot_fallback_to_first_record():
    x = quote([obs(5)], hours=4)
    assert x.status == "unavailable"
    assert x.rate is None
    assert x.eligible_count == 0
    assert not x.capital_authority_granted
    assert x.evidence_sha256 == quote([], hours=4).evidence_sha256


def test_rate_is_not_known_until_it_is_observed_even_if_effective_earlier():
    x = obs(5, observed_hours=7)
    assert quote([x], hours=6).rate is None
    assert quote([x], hours=7).rate == Decimal("0.001")
    assert quote([x], hours=7).effective_at == T0 + timedelta(hours=5)


def test_last_known_rate_never_sees_later_record_or_hashes_its_contents():
    older = obs(2, rate="0.001")
    future = obs(10, rate="-0.9", sha="b" * 64)
    at_4 = quote([older], hours=4)
    with_future = quote([older, future], hours=4)
    assert at_4 == with_future
    assert quote([older, future], hours=11).rate == Decimal("-0.9")


def test_stale_evidence_denied_instead_of_reusing_old_funding():
    at_20 = quote([obs(2)], hours=20, max_age=timedelta(hours=12))
    assert at_20.status == "stale"
    assert at_20.rate is None
    with pytest.raises(PermissionError, match="unavailable"):
        funding_cashflow_estimate(at_20, signed_notional=Decimal("500"))


def test_cashflow_sign_and_decimal_precision_are_non_authoritative():
    q = quote([obs(2)], hours=2)
    assert funding_cashflow_estimate(
        q, signed_notional=Decimal("300")
    ) == Decimal("-0.300")
    assert funding_cashflow_estimate(
        q, signed_notional=Decimal("-300")
    ) == Decimal("0.300")
    assert funding_cashflow_estimate(
        q, signed_notional=Decimal("0")
    ) == Decimal("0.000")
    assert not q.capital_authority_granted


def test_unsorted_or_duplicated_settlement_tape_is_rejected():
    with pytest.raises(ValueError, match="strictly increasing"):
        quote([obs(4), obs(2)], hours=6)
    with pytest.raises(ValueError, match="strictly increasing"):
        quote([obs(2), obs(2, observed_hours=3)], hours=6)


def test_instrument_cross_contamination_is_rejected():
    with pytest.raises(ValueError, match="instrument"):
        resolve_funding_history(
            [obs(2)],
            instrument_id="ETH-USDT-PERP",
            as_of=T0 + timedelta(hours=3),
        )


@pytest.mark.parametrize("bad", [float("nan"), Decimal("Infinity"), True, "0.01"])
def test_rate_must_be_finite_decimal_not_coercible_number(bad):
    with pytest.raises(ValueError, match="rate"):
        FundingObservation("BTC-USDT-PERP", T0, T0, bad, H)


def test_nan_or_naive_timestamps_are_rejected():
    with pytest.raises(ValueError, match="timezone"):
        FundingObservation(
            "BTC-USDT-PERP", T0.replace(tzinfo=None), T0, Decimal("0.1"), H
        )
    with pytest.raises(ValueError, match="timezone"):
        resolve_funding_history(
            [], instrument_id="BTC-USDT-PERP", as_of=T0.replace(tzinfo=None)
        )


def test_observation_cannot_predate_settled_funding_event():
    with pytest.raises(ValueError, match="observed_at"):
        obs(3, observed_hours=2)


def test_rate_source_digest_is_canonical_hex():
    with pytest.raises(ValueError, match="source_sha256"):
        obs(1, sha="zz" * 32)


def test_boolean_and_nonpositive_max_age_are_rejected():
    with pytest.raises(ValueError, match="max_age"):
        quote([obs(1)], hours=2, max_age=timedelta(0))
    with pytest.raises(ValueError, match="max_age"):
        quote([obs(1)], hours=2, max_age="12h")


def test_signed_notional_or_intervals_cannot_be_invalid_or_boolean():
    q = quote([obs(2)], hours=2)
    for invalid in [True, 0.01, Decimal("NaN")]:
        with pytest.raises(ValueError, match="signed_notional"):
            funding_cashflow_estimate(q, signed_notional=invalid)
    for invalid in [True, -1, 0, 10001]:
        with pytest.raises(ValueError, match="intervals"):
            funding_cashflow_estimate(
                q, signed_notional=Decimal("100"), intervals=invalid
            )


def test_utc_and_explicit_offset_observations_produce_identical_receipts():
    tz = timezone(timedelta(hours=2))
    a = obs(2)
    b = FundingObservation(
        a.instrument_id,
        a.effective_at.astimezone(tz),
        a.observed_at.astimezone(tz),
        a.rate,
        a.source_sha256,
    )
    assert quote([a], hours=3).evidence_sha256 == quote([b], hours=3).evidence_sha256

def test_one_observed_rate_cannot_be_reused_as_unobserved_future_intervals():
    one_rate = quote([obs(2)], hours=2)
    with pytest.raises(ValueError, match="single observed interval"):
        funding_cashflow_estimate(
            one_rate, signed_notional=Decimal("500"), intervals=2,
        )
