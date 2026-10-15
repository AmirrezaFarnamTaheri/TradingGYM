"""Sealed holdout scoreboards must not rank incomparable or leaking runs."""

from dataclasses import replace

import pytest

from trading_gym.experiments.paired_research import (
    ResearchRun,
    compare_paired_research,
)


def _scenario(
    model,
    seed,
    *,
    gross=None,
    cost=0.005,
    tape="a" * 64,
    available=1700000100,
    split="holdout",
    start=1700000000,
    end=1700000050,
    source="b" * 64,
):
    return ResearchRun(
        candidate_id=model,
        seed=seed,
        regime="bull" if seed % 2 else "bear",
        tape_sha256=tape,
        source_sha256=source,
        started_at_unix=start,
        ended_at_unix=end,
        label_available_at_unix=available,
        gross_return=(0.01 if model == "base" else 0.02) if gross is None else gross,
        cost_fraction=cost,
        split=split,
    )


def _runs():
    return [
        _scenario(model, seed)
        for model in ("base", "alpha", "beta")
        for seed in range(1, 5)
    ]


def _compare(rows, **changes):
    return compare_paired_research(
        baseline_id="base", runs=rows, as_of_unix=1700000100, **changes
    )


def test_net_of_cost_sign_test_and_holm_adjustment_are_explicitly_non_authoritative():
    rows = [
        replace(r, gross_return=0.0) if r.candidate_id == "beta" else r
        for r in _runs()
    ]
    report = _compare(rows)
    alpha, beta = report["candidates"]
    assert alpha["candidate_id"] == "alpha"
    assert alpha["nominal_one_sided_p_value"] == pytest.approx(0.0625)
    assert alpha["holm_adjusted_p_value"] == pytest.approx(0.125)
    assert alpha["median_net_excess"] == pytest.approx(0.01)
    assert beta["nominal_one_sided_p_value"] == 1.0
    assert beta["holm_adjusted_p_value"] == 1.0
    assert report["statistical_independence_verified"] is False
    assert report["selection_authority_granted"] is False
    assert report["promotion_authority_granted"] is False
    assert report["funded_live_eligible"] is False


def test_report_hash_is_order_independent_and_source_sensitive():
    rows = _runs()
    first = _compare(rows)
    assert first == _compare(list(reversed(rows)))
    revised = list(rows)
    revised[0] = replace(revised[0], source_sha256="c" * 64)
    assert first["report_sha256"] != _compare(revised)["report_sha256"]


@pytest.mark.parametrize(
    "change",
    [
        {"tape_sha256": "c" * 64},
        {"started_at_unix": 1700000001},
        {"ended_at_unix": 1700000051},
        {"label_available_at_unix": 1700000101},
        {"split": "train"},
        {"gross_return": float("nan")},
        {"cost_fraction": float("inf")},
        {"cost_fraction": -1.0},
        {"cost_fraction": None},
        {"source_sha256": "unsealed"},
        {"seed": True},
    ],
)
def test_mismatched_data_tapes_costs_labels_or_splits_denied(change):
    rows = _runs()
    rows[4] = replace(rows[4], **change)
    with pytest.raises(ValueError):
        _compare(rows)


def test_duplicate_or_incomplete_candidate_scenarios_cannot_be_scored():
    rows = _runs()
    with pytest.raises(ValueError, match="duplicate"):
        _compare(rows + [rows[4]])
    with pytest.raises(ValueError, match="incomparable"):
        _compare(rows[:-1])


def test_ties_do_not_create_fake_significance():
    rows = [replace(r, gross_return=0.01) for r in _runs()]
    result = _compare(rows)
    assert all(c["non_tied_pairs"] == 0 for c in result["candidates"])
    assert all(
        c["nominal_one_sided_p_value"] == 1.0
        for c in result["candidates"]
    )


def test_require_enough_historical_pairs_and_distinct_regimes():
    with pytest.raises(ValueError, match="insufficient"):
        _compare(_runs(), minimum_pairs=5)
    with pytest.raises(ValueError, match="regimes"):
        _compare(_runs(), minimum_regimes=3)
