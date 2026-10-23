"""A factor needs as-of membership and filings before research qualification."""

from decimal import Decimal

import pytest

from trading_gym.market.factor_research_readiness import (
    assess_factor_research_readiness,
)
from trading_gym.market.fundamental_asof import FundamentalFact, FundamentalQuery
from trading_gym.market.universe_asof import UniverseMembershipEvent

DAY = 86400


def _facts():
    return [
        FundamentalFact(
            "NASDAQ:AAA",
            "net_income",
            "annual",
            100 * DAY,
            465 * DAY,
            490 * DAY,
            493 * DAY,
            Decimal("100"),
            "USD",
            "a" * 64,
        )
    ]


def _members(included=True, observed=97 * DAY):
    return [
        UniverseMembershipEvent(
            "SP500",
            "NASDAQ:AAA",
            100 * DAY,
            95 * DAY,
            observed,
            included,
            "b" * 64,
        )
    ]


def _queries():
    return [FundamentalQuery("NASDAQ:AAA", "net_income", "annual", "USD")]


def _assess(facts=None, membership=None, when=500 * DAY, requested=None):
    return assess_factor_research_readiness(
        fundamental_facts=_facts() if facts is None else facts,
        fundamental_queries=_queries() if requested is None else requested,
        membership_events=_members() if membership is None else membership,
        universe_id="SP500",
        instruments=["NASDAQ:AAA"],
        as_of_unix=when,
    )


def test_both_evidence_planes_are_required_for_data_readiness():
    report = _assess()
    assert report["ready_for_research"] is True
    assert report["factor_authority_granted"] is False
    assert report["execution_authority_granted"] is False
    assert len(report["report_sha256"]) == 64


def test_missing_fundamental_blocks_even_known_member():
    report = _assess(facts=[])
    assert not report["ready_for_research"]
    assert "fundamental_unavailable:NASDAQ:AAA:net_income:annual" in report["blockers"]


@pytest.mark.parametrize(
    "membership",
    [
        _members(False),
        [],
        _members(True, observed=520 * DAY),
    ],
)
def test_excluded_or_unobserved_constituent_cannot_qualify_factor(membership):
    report = _assess(membership=membership)
    assert report["ready_for_research"] is False
    assert report["execution_authority_granted"] is False


def test_unrelated_instrument_query_denied_instead_of_cross_universe_join():
    with pytest.raises(ValueError, match="universe|instrument"):
        _assess(
            requested=[FundamentalQuery("NYSE:OTHER", "net_income", "annual", "USD")]
        )


def test_future_filing_and_membership_are_not_used_by_past_snapshot():
    assert _assess(when=100 * DAY)["ready_for_research"] is False


def test_report_receipt_ignores_future_only_revisions():
    baseline = _assess()
    future = _members(False, observed=600 * DAY)
    assert _assess(membership=_members() + future) == baseline
    assert _assess(facts=[])["report_sha256"] != baseline["report_sha256"]


def test_market_package_exposes_single_canonical_pit_ownership():
    import trading_gym.market as public
    from trading_gym.market.fundamental_asof import assess_fundamentals_asof
    from trading_gym.market.universe_asof import assess_universe_asof

    assert public.assess_fundamentals_asof is assess_fundamentals_asof
    assert public.assess_universe_asof is assess_universe_asof
    assert public.assess_factor_research_readiness is assess_factor_research_readiness
