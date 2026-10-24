"""Historical membership cannot be inferred from the current surviving list."""

from dataclasses import replace

import pytest

from trading_gym.market.universe_asof import (
    UniverseMembershipEvent,
    assess_universe_asof,
)

DAY = 86400


def event(**changes):
    row = dict(
        universe_id="SP500",
        instrument_id="NYSE:XYZ",
        effective_at_unix=100 * DAY,
        published_at_unix=95 * DAY,
        observed_at_unix=97 * DAY,
        included=True,
        source_sha256="a" * 64,
    )
    row.update(changes)
    return UniverseMembershipEvent(**row)


def assess(events, as_of=150 * DAY, instruments=None):
    return assess_universe_asof(
        events=events,
        universe_id="SP500",
        instruments=instruments if instruments is not None else ["NYSE:XYZ"],
        as_of_unix=as_of,
    )


def test_current_membership_never_backfills_an_earlier_historical_replay():
    r = assess([event()], as_of=90 * DAY)
    assert r["ready_for_research"] is False
    assert r["members"][0]["state"] == "unknown"
    assert "membership_unknown:NYSE:XYZ" in r["blockers"]
    assert r["survivorship_bias_not_independently_ruled_out"] is True


def test_published_but_unobserved_exclusion_does_not_leak():
    excluded = event(
        effective_at_unix=200 * DAY,
        published_at_unix=190 * DAY,
        observed_at_unix=205 * DAY,
        included=False,
        source_sha256="b" * 64,
    )
    before = assess([event(), excluded], as_of=202 * DAY)
    assert before["members"][0]["state"] == "included"
    after = assess([event(), excluded], as_of=205 * DAY)
    assert after["members"][0]["state"] == "excluded"


def test_future_effective_membership_not_applied_early():
    future = event(
        effective_at_unix=200 * DAY,
        published_at_unix=180 * DAY,
        observed_at_unix=181 * DAY,
    )
    assert assess([future], as_of=185 * DAY)["members"][0]["state"] == "unknown"
    assert assess([future], as_of=200 * DAY)["members"][0]["state"] == "included"


def test_old_period_restatement_cannot_override_newer_effective_membership():
    old_restatement = event(
        effective_at_unix=80 * DAY,
        published_at_unix=220 * DAY,
        observed_at_unix=222 * DAY,
        included=False,
        source_sha256="b" * 64,
    )
    report = assess([event(), old_restatement], as_of=230 * DAY)
    assert report["members"][0]["state"] == "included"


def test_same_effective_date_correction_applies_only_when_known():
    corrected = event(
        effective_at_unix=100 * DAY,
        published_at_unix=160 * DAY,
        observed_at_unix=161 * DAY,
        included=False,
        source_sha256="b" * 64,
    )
    earlier = assess([event(), corrected], as_of=150 * DAY)
    assert earlier["members"][0]["state"] == "included"
    later = assess([event(), corrected], as_of=161 * DAY)
    assert later["members"][0]["state"] == "excluded"


def test_sorted_replay_receipt_is_content_bound_and_never_grants_trade():
    first = event()
    second = event(instrument_id="NASDAQ:ABC", source_sha256="b" * 64)
    a = assess([first, second], instruments=["NASDAQ:ABC", "NYSE:XYZ"])
    b = assess([second, first], instruments=["NYSE:XYZ", "NASDAQ:ABC"])
    assert a == b
    assert len(a["report_sha256"]) == 64
    assert a["execution_authority_granted"] is False
    assert a["ready_for_research"] is True


def test_future_membership_change_never_alters_historical_receipt():
    prior = assess([event()], as_of=150 * DAY)
    future = event(
        effective_at_unix=200 * DAY,
        published_at_unix=190 * DAY,
        observed_at_unix=210 * DAY,
        included=False,
        source_sha256="b" * 64,
    )
    assert assess([event(), future], as_of=150 * DAY) == prior


def test_identical_timeline_conflict_is_rejected_not_silently_won():
    first = event()
    altered = replace(first, included=False, source_sha256="b" * 64)
    with pytest.raises(ValueError, match="duplicate|conflict"):
        assess([first, altered])


@pytest.mark.parametrize(
    "bad",
    [
        {"included": "true"},
        {"included": 1},
        {"source_sha256": "XYZ"},
        {"universe_id": "sp500"},
        {"instrument_id": "NYSE:B A D"},
        {"observed_at_unix": 94 * DAY},
        {"effective_at_unix": True},
        {"published_at_unix": -1},
    ],
)
def test_malformed_membership_rejected(bad):
    with pytest.raises(ValueError):
        assess([event(**bad)])


def test_unknown_names_are_not_silently_treated_as_excluded():
    report = assess([event()], instruments=["NYSE:XYZ", "NASDAQ:MISS"])
    assert report["ready_for_research"] is False
    assert "membership_unknown:NASDAQ:MISS" in report["blockers"]
    assert report["members"][0]["state"] == "unknown"


def test_duplicate_requests_and_boolean_time_are_denied():
    with pytest.raises(ValueError, match="duplicate"):
        assess([], instruments=["NYSE:XYZ", "NYSE:XYZ"])
    with pytest.raises(ValueError):
        assess([], as_of=True)
