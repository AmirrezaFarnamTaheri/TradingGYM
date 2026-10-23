"""Research strategy ranking must remain sensitive to plausible execution costs."""

import pytest

from trading_gym.experiments.cost_sensitivity import compare_cost_scenarios
from trading_gym.experiments.peer_strategy_comparison import StrategyReplay


def _rows():
    times = tuple(range(2000, 2040))

    def row(identifier, gross_bps, turnover, digest):
        return StrategyReplay(
            strategy_id=identifier,
            strategy_sha256=digest * 64,
            market_sha256="f" * 64,
            bar_close_unix=times,
            gross_return_bps=tuple([gross_bps] * 40),
            turnover_notional=tuple([turnover] * 40),
        )

    return [row("baseline", 5.0, 0.0, "a"), row("aggressive", 10.0, 2.0, "b")]


def _compare(rows, costs):
    return compare_cost_scenarios(
        rows,
        baseline_id="baseline",
        train_end_unix=1999,
        as_of_unix=2200,
        scenarios=costs,
    )


def test_high_turnover_winner_reverses_under_realistic_cost_stress():
    report = _compare(_rows(), [(0.0, 0.0), (3.0, 2.0), (8.0, 5.0)])
    assert report["selection_authority"] == "none"
    assert report["eligible_for_funded_live"] is False
    assert report["scenarios"][0]["best_net_strategy_id"] == "aggressive"
    assert report["scenarios"][-1]["best_net_strategy_id"] == "baseline"
    assert report["scenarios"][-1]["fee_bps"] == 8.0
    assert "gross_return_bps" not in str(report)
    assert len(report["report_sha256"]) == 64


def test_scenario_and_replay_permutation_cannot_change_result():
    costs = [(8, 5), (0, 0), (3, 2)]
    source = _rows()
    assert _compare(source, costs) == _compare(source[::-1], costs[::-1])


@pytest.mark.parametrize(
    "bad",
    [
        [(0, 0)],
        [(0, 0)] * 17,
        [(0, 0), (0.0, 0.0)],
        [(True, 0), (1, 0)],
        [(float("nan"), 0), (1, 0)],
        [(0, 0), (-1, 0)],
    ],
)
def test_incomplete_or_invalid_cost_grid_is_denied(bad):
    with pytest.raises(ValueError):
        _compare(_rows(), bad)
