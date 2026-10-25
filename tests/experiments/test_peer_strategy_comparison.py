"""A paired paper comparison must not be mistaken for capital approval."""

from dataclasses import replace
import math
import pytest

from trading_gym.rl.experiments.peer_strategy_comparison import (
    StrategyReplay,
    compare_strategy_replays,
)


def replay(strategy_id, *, gross=None, turnover=None, market=None, times=None, **other):
    size = 40
    return StrategyReplay(
        strategy_id=strategy_id,
        strategy_sha256=("a" if strategy_id == "baseline" else "b") * 64,
        market_sha256=market or "f" * 64,
        bar_close_unix=times or tuple(range(2000, 2000 + size)),
        gross_return_bps=gross or tuple([10.0] * size),
        turnover_notional=turnover or tuple([0.5] * size),
        **other,
    )


def compare(candidates, **kw):
    settings = {
        "baseline_id": "baseline",
        "train_end_unix": 1999,
        "as_of_unix": 2200,
        "fee_bps": 5.0,
        "slippage_bps": 2.0,
    }
    settings.update(kw)
    return compare_strategy_replays(candidates, **settings)


def test_same_gross_returns_penalize_high_turnover_after_fees():
    expensive = replay("candidate", turnover=tuple([2.0] * 40))
    baseline = replay("baseline", turnover=tuple([0.5] * 40))
    report = compare([expensive, baseline])
    assert report["eligible_for_funded_live"] is False
    assert report["selection_authority"] == "none"
    assert report["results"][0]["strategy_id"] == "baseline"
    candidate = next(r for r in report["results"] if r["strategy_id"] == "candidate")
    assert candidate["paired_mean_edge_bps_vs_baseline"] == pytest.approx(-10.5)
    assert candidate["estimated_cost_bps"] == pytest.approx(560)
    assert len(report["report_sha256"]) == 64
    assert "gross_return_bps" not in str(report)


def test_replay_receipt_is_order_invariant_and_evidence_sensitive():
    base, candidate = replay("baseline"), replay("candidate")
    left = compare([base, candidate])
    right = compare([candidate, base])
    assert left == right
    changed = replace(candidate, turnover_notional=tuple([0.75] * 40))
    assert compare([base, changed])["report_sha256"] != left["report_sha256"]


@pytest.mark.parametrize(
    "changed",
    [
        {"bar_close_unix": tuple([2039] + list(range(2001, 2040)))},
        {"bar_close_unix": tuple(range(3000, 3040))},
        {"market_sha256": "e" * 64},
        {"split": "train"},
        {"strategy_sha256": "not-a-hash"},
        {"gross_return_bps": tuple([math.nan] + [10.0] * 39)},
        {"gross_return_bps": tuple([False] + [10.0] * 39)},
        {"turnover_notional": tuple([-0.1] + [0.5] * 39)},
        {"turnover_notional": tuple([math.inf] + [0.5] * 39)},
    ],
)
def test_unsealed_or_incomparable_candidate_is_rejected(changed):
    base = replay("baseline")
    candidate = replace(replay("candidate"), **changed)
    with pytest.raises(ValueError):
        compare([base, candidate])


def test_future_and_pretraining_boundary_are_strict():
    with pytest.raises(ValueError):
        compare([replay("baseline"), replay("candidate")], as_of_unix=2010)
    with pytest.raises(ValueError):
        compare([replay("baseline"), replay("candidate")], train_end_unix=2000)


def test_duplicate_id_no_baseline_and_bad_fee_fail_closed():
    with pytest.raises(ValueError):
        compare([replay("baseline"), replay("baseline")])
    with pytest.raises(ValueError):
        compare([replay("candidate"), replay("other")])
    with pytest.raises(ValueError):
        compare_strategy_replays(
            [replay("baseline"), replay("candidate")],
            baseline_id="baseline",
            train_end_unix=1999,
            as_of_unix=2200,
            fee_bps=True,
        )


def test_no_turnover_is_not_assumed_no_exposure_or_promotable():
    replayed = replay("candidate", turnover=tuple([0.0] * 40))
    report = compare([replay("baseline"), replayed])
    row = next(x for x in report["results"] if x["strategy_id"] == "candidate")
    assert row["no_observed_turnover"] is True
    assert row["eligible_for_funded_live"] is False


def test_net_bankruptcy_denied_without_clamping():
    catastrophic = replay("candidate", gross=tuple([-10000.0] + [0.0] * 39))
    with pytest.raises(ValueError):
        compare([replay("baseline"), catastrophic])


def test_multiple_candidates_block_normalization_remains_nonpromotable():
    baseline = replay("baseline")
    x = replay("alpha", gross=tuple([9.0] * 40))
    y = replay("beta", gross=tuple([11.0] * 40))
    # Explicit unique strategy digests for each candidate.
    x = replace(x, strategy_sha256="c" * 64)
    y = replace(y, strategy_sha256="d" * 64)
    report = compare([x, baseline, y])
    assert report["comparisons"] == 2
    assert report["eligible_for_funded_live"] is False
    assert all(len(x["paired_block_interval_bps"]) == 2 for x in report["results"])
    assert report["results"][0]["strategy_id"] == "beta"
    assert report["results"][-1]["strategy_id"] == "alpha"


def test_same_artifact_under_different_ids_not_two_independent_strategies():
    alias = replace(replay("candidate"), strategy_sha256="a" * 64)
    with pytest.raises(ValueError, match="same strategy artifact"):
        compare([replay("baseline"), alias])


def test_invalid_integer_as_of_and_oversized_workload_denied():
    with pytest.raises(ValueError):
        compare([replay("baseline"), replay("candidate")], as_of_unix=True)
    with pytest.raises(ValueError):
        compare([replay("baseline"), replay("candidate")], fee_bps=math.inf)
    with pytest.raises(ValueError):
        compare([replay("baseline")] * 17)


def test_mixed_costs_change_ranking_but_not_source_data():
    base, candidate = replay("baseline"), replay("candidate")
    zero = compare([base, candidate], fee_bps=0.0, slippage_bps=0.0)
    expensive = compare([base, replace(candidate, turnover_notional=tuple([2.0] * 40))])
    assert zero["report_sha256"] != expensive["report_sha256"]
    assert base.gross_return_bps == tuple([10.0] * 40)


def test_unbounded_aggregate_return_is_rejected_without_overflow():
    timestamps = tuple(range(2000, 2400))
    winner = replay(
        "candidate",
        gross=tuple([100000.0] * 400),
        turnover=tuple([0.0] * 400),
        times=timestamps,
    )
    baseline = replay(
        "baseline",
        gross=tuple([10.0] * 400),
        turnover=tuple([0.5] * 400),
        times=timestamps,
    )
    with pytest.raises(ValueError, match="compounded"):
        compare([baseline, winner], as_of_unix=2500)
