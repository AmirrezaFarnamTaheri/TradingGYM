"""Point-in-time fundamental research: no future filing or implicit unit conversion."""

from dataclasses import replace
from decimal import Decimal

import pytest

from trading_gym.market.fundamental_asof import (
    FundamentalFact,
    FundamentalQuery,
    assess_fundamentals_asof,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
DAY = 86400


def fact(**changes):
    values = dict(
        instrument_id="NASDAQ:AAPL", field="net_income", period_type="annual",
        period_start_unix=100 * DAY, period_end_unix=465 * DAY,
        filed_at_unix=490 * DAY, observed_at_unix=493 * DAY,
        value=Decimal("125.5"), unit="USD", source_sha256=SHA_A,
    )
    values.update(changes)
    return FundamentalFact(**values)


def query(**changes):
    values = dict(
        instrument_id="NASDAQ:AAPL", field="net_income",
        period_type="annual", unit="USD",
    )
    values.update(changes)
    return FundamentalQuery(**values)


def assess(facts, queries=None, as_of=500 * DAY, **options):
    return assess_fundamentals_asof(
        facts=facts,
        queries=queries if queries is not None else [query()],
        as_of_unix=as_of,
        **options,
    )


@pytest.mark.parametrize("moment", [465 * DAY, 490 * DAY, 493 * DAY - 1])
def test_period_end_and_preobservation_file_date_cannot_leak(moment):
    report = assess([fact()], as_of=moment)
    assert report["ready_for_research"] is False
    assert report["facts"][0]["value"] is None
    assert report["blockers"] == [
        "fundamental_unavailable:NASDAQ:AAPL:net_income:annual"
    ]


def test_filing_is_available_only_after_actual_observation():
    report = assess([fact()], as_of=493 * DAY)
    assert report["facts"][0]["value"] == "125.5"
    assert report["facts"][0]["available_at_unix"] == 493 * DAY
    assert report["ready_for_research"] is True
    assert report["research_only"] is True
    assert report["factor_authority_granted"] is False
    assert report["execution_authority_granted"] is False


def test_later_restatement_only_selected_after_it_is_observed():
    original = fact()
    restated = fact(
        filed_at_unix=520 * DAY, observed_at_unix=525 * DAY,
        value=Decimal("130"), source_sha256=SHA_B,
    )
    assert assess([restated, original], as_of=510 * DAY)["facts"][0]["value"] == "125.5"
    assert assess([original, restated], as_of=520 * DAY)["facts"][0]["value"] == "125.5"
    late = assess([original, restated], as_of=525 * DAY)
    assert late["facts"][0]["value"] == "130"
    assert late["facts"][0]["source_sha256"] == SHA_B


def test_later_fiscal_period_outranks_older_period_restatement():
    newer = fact(
        period_start_unix=465 * DAY, period_end_unix=830 * DAY,
        filed_at_unix=850 * DAY, observed_at_unix=851 * DAY,
        value=Decimal("200"),
    )
    older_restatement = fact(
        filed_at_unix=860 * DAY, observed_at_unix=861 * DAY,
        value=Decimal("190"), source_sha256=SHA_B,
    )
    report = assess([newer, older_restatement], as_of=900 * DAY)
    assert report["facts"][0]["period_end_unix"] == 830 * DAY
    assert report["facts"][0]["value"] == "200"


@pytest.mark.parametrize(
    "different_query",
    [query(unit="EUR"), query(period_type="quarter")],
)
def test_reporting_units_and_fiscal_frames_are_not_silently_converted(
    different_query,
):
    result = assess([fact()], queries=[different_query])
    assert result["facts"][0]["value"] is None
    assert not result["ready_for_research"]


def test_stale_filing_is_not_forward_filled_forever():
    report = assess(
        [fact()], as_of=900 * DAY, max_period_age_seconds=400 * DAY,
    )
    assert report["facts"][0]["value"] is None
    assert "fundamental_stale:NASDAQ:AAPL:net_income:annual" in report["blockers"]


def test_same_filing_version_with_conflicting_value_is_ambiguous():
    original = fact()
    with pytest.raises(ValueError, match="conflicting"):
        assess([
            original,
            replace(original, value=Decimal("900"), source_sha256=SHA_B),
        ])


def test_reordering_inputs_does_not_change_report_or_receipt():
    a = fact()
    b = fact(instrument_id="NYSE:IBM", value=Decimal("50"))
    reqs = [query(instrument_id="NYSE:IBM"), query()]
    original = assess([a, b], queries=reqs)
    reordered = assess([b, a], queries=list(reversed(reqs)))
    assert original == reordered
    assert len(original["report_sha256"]) == len(original["input_sha256"]) == 64
    modified = assess([replace(a, value=Decimal("129")), b], queries=reqs)
    assert modified["report_sha256"] != original["report_sha256"]


@pytest.mark.parametrize(
    "change",
    [
        {"value": Decimal("NaN")}, {"value": Decimal("Infinity")},
        {"value": True}, {"value": 1.0},
        {"source_sha256": "NOT-A-HASH"},
        {"field": "net income"}, {"unit": "usd"},
        {"period_end_unix": True},
        {"filed_at_unix": 100 * DAY},
        {"observed_at_unix": 489 * DAY},
    ],
)
def test_malformed_filing_evidence_fails_closed(change):
    with pytest.raises(ValueError):
        assess([fact(**change)])


def test_future_facts_are_allowed_in_dataset_but_not_historical_results():
    original = fact()
    future = fact(
        period_start_unix=465 * DAY, period_end_unix=830 * DAY,
        filed_at_unix=851 * DAY, observed_at_unix=852 * DAY,
        value=Decimal("9000"), source_sha256=SHA_B,
    )
    early = assess([original], as_of=510 * DAY)
    assert assess([original, future], as_of=510 * DAY) == early


def test_empty_data_and_duplicate_queries_are_not_qualified():
    assert assess([])["ready_for_research"] is False
    with pytest.raises(ValueError, match="duplicate"):
        assess([fact()], queries=[query(), query()])


def test_invalid_or_oversized_input_denied():
    with pytest.raises(ValueError):
        assess([{"period_end": 10}])
    with pytest.raises(ValueError):
        assess([fact()], as_of=True)
    with pytest.raises(ValueError):
        assess([fact()], queries=[query()] * 2000)


def test_quarterly_flow_kept_independent_from_annual():
    quarterly = fact(
        period_type="quarter", period_start_unix=400 * DAY,
        period_end_unix=465 * DAY, value=Decimal("20"),
    )
    report = assess([fact(), quarterly], queries=[query(period_type="quarter")])
    assert report["facts"][0]["value"] == "20"


@pytest.mark.parametrize(
    "kind,start,end",
    [
        ("annual", 400 * DAY, 465 * DAY),
        ("quarter", 100 * DAY, 465 * DAY),
        ("instant", 100 * DAY, 465 * DAY),
    ],
)
def test_fiscal_frame_mismatch_cannot_mix_ytd_quarter_and_annual(kind, start, end):
    with pytest.raises(ValueError, match="frame|duration"):
        assess([
            fact(
                period_type=kind,
                period_start_unix=start,
                period_end_unix=end,
            )
        ])


def test_replay_never_uses_next_years_filing():
    new = fact(
        period_start_unix=465 * DAY, period_end_unix=830 * DAY,
        filed_at_unix=850 * DAY, observed_at_unix=853 * DAY,
        value=Decimal("200"),
    )
    assert assess([fact(), new], as_of=520 * DAY)["facts"][0]["value"] == "125.5"
