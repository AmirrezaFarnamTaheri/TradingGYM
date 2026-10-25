"""Statistical validation battery (PSR/DSR/MinTRL) and CSCV/PBO tests.

Synthetic-data sanity anchors, after Bailey & López de Prado:
- A long-only high-Sharpe series must score PSR≈1 against a zero benchmark.
- DSR must be strictly lower than PSR when many trials compete (deflation).
- CSCV/PBO on pure-noise configs must land near 0.5; on a genuinely dominant
  config it must collapse toward 0.
"""

import numpy as np
import pytest

from trading_gym.agent.research.cscv_pbo import cscv_pbo_analysis, pbo_summary
from trading_gym.agent.research.statistical_validation import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    full_statistical_report,
    min_track_record_length,
    probabilistic_sharpe_ratio,
    sharpe_std_error_factor,
)


def _returns(n: int, mean_daily: float, seed: int, vol: float = 0.01) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(mean_daily, vol, size=n)


def _sharpe(returns: np.ndarray) -> float:
    std = returns.std(ddof=1)
    return float(returns.mean() / std) if std > 0 else 0.0


# ---------------------------------------------------------------------------
# PSR / SE factor
# ---------------------------------------------------------------------------


def test_sharpe_std_error_factor_normal_case_is_one():
    # Zero skew, normal kurtosis (excess 0) and SR→0 give exactly 1.
    assert sharpe_std_error_factor(0.0) == pytest.approx(1.0)


def test_sharpe_std_error_factor_rejects_degenerate_inputs():
    # 1 - skew·SR + ((k-1)/4)·SR² <= 0 for this impossible moment triple.
    with pytest.raises(ValueError):
        sharpe_std_error_factor(2.5, skew=6.0, kurtosis=5.0)
    with pytest.raises(ValueError):
        sharpe_std_error_factor(float("nan"))


def test_psr_high_sharpe_scores_near_one():
    rets = _returns(504, mean_daily=0.002, seed=11)
    psr = probabilistic_sharpe_ratio(
        _sharpe(rets),
        n_obs=len(rets),
        skew=float(_skew(rets)),
        kurtosis=float(_excess_kurtosis(rets)),
    )
    assert psr > 0.99


def test_psr_zero_edge_sharpe_is_about_half_for_normal_returns():
    psr = probabilistic_sharpe_ratio(0.0, n_obs=252)
    assert psr == pytest.approx(0.5, abs=0.02)


def test_psr_accepts_fisher_excess_kurtosis_conversion():
    flat = probabilistic_sharpe_ratio(1.0, n_obs=100, kurtosis=0.0)
    fat = probabilistic_sharpe_ratio(1.0, n_obs=100, kurtosis=6.0)
    assert 0.5 < flat < 1.0
    assert fat < flat  # fatter tails reduce confidence in the same SR


def _skew(returns: np.ndarray) -> float:
    centered = returns - returns.mean()
    m2 = float((centered**2).mean())
    m3 = float((centered**3).mean())
    return m3 / m2**1.5


def _excess_kurtosis(returns: np.ndarray) -> float:
    centered = returns - returns.mean()
    m2 = float((centered**2).mean())
    m4 = float((centered**4).mean())
    return m4 / m2**2 - 3.0


# ---------------------------------------------------------------------------
# Expected max SR / DSR
# ---------------------------------------------------------------------------


def test_expected_max_sharpe_requires_exactly_one_spread_input():
    with pytest.raises(ValueError):
        expected_max_sharpe(n_trials=10)
    with pytest.raises(ValueError):
        expected_max_sharpe(n_trials=10, sharpe_std=1.0, sharpe_variance=1.0)
    assert expected_max_sharpe(n_trials=1, sharpe_std=1.0) == 0.0
    assert expected_max_sharpe(n_trials=100, sharpe_std=1.0) > 2.0


def test_dsr_deflates_below_psr_with_many_trials():
    rets = _returns(120, mean_daily=0.001, seed=23, vol=0.005)
    sr = _sharpe(rets)
    assert 0.05 < sr < 1.0  # fixture sanity
    psr = probabilistic_sharpe_ratio(sr, n_obs=len(rets))
    assert 0.5 < psr < 1.0  # unsaturated so the ordering below is meaningful
    dsr = deflated_sharpe_ratio(
        sr,
        n_trials=200,
        n_obs=len(rets),
        trial_sharpe_std=0.15,
    )
    assert 0.0 <= dsr < psr <= 1.0


def test_dsr_matches_psr_when_benchmark_is_zero_single_trial():
    rets = _returns(300, mean_daily=0.001, seed=31)
    sr = _sharpe(rets)
    psr = probabilistic_sharpe_ratio(sr, n_obs=300)
    dsr = deflated_sharpe_ratio(sr, n_trials=1, n_obs=300, trial_sharpe_std=0.0)
    assert dsr == pytest.approx(psr)


# ---------------------------------------------------------------------------
# MinTRL
# ---------------------------------------------------------------------------


def test_min_track_record_length_grows_as_edge_shrinks():
    strong = min_track_record_length(1.5)
    weak = min_track_record_length(0.1)
    assert weak > strong > 1.0


def test_min_track_record_length_infinite_without_edge():
    assert min_track_record_length(0.0) == float("inf")


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------


def test_full_report_flags_and_optional_dsr():
    rets = _returns(504, mean_daily=0.004, seed=41, vol=0.005)
    report = full_statistical_report(
        _sharpe(rets),
        n_obs=len(rets),
        skew=_skew(rets),
        kurtosis=_excess_kurtosis(rets),
        n_trials=20,
        trial_sharpe_std=0.10,
    )
    assert report.psr_pass is True
    assert report.dsr_pass is True
    assert report.deflated_sharpe_ratio is not None
    assert report.deflated_sharpe_ratio <= report.probabilistic_sharpe_ratio

    bare = full_statistical_report(_sharpe(rets), n_obs=len(rets))
    assert bare.dsr_pass is None and bare.deflated_sharpe_ratio is None


# ---------------------------------------------------------------------------
# CSCV / PBO
# ---------------------------------------------------------------------------


def _noise_matrix(configs: int, partitions: int, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    return {
        f"cfg_{i}": rng.normal(0.0005, 0.01, size=partitions) for i in range(configs)
    }


def test_cscv_pbo_iid_entries_rank_uniformly():
    # Fully independent entries contain no persistent config skill, so the
    # IS winner's out-of-sample rank must come out unbiased: PBO near one
    # half, well away from the overfitting alarm zone.
    matrix = _noise_matrix(12, 24, seed=99)
    result = cscv_pbo_analysis(matrix)
    summary = pbo_summary(result, threshold=0.6)
    assert summary["pbo"] == pytest.approx(0.5, abs=0.2)
    assert summary["overfit_flag"] is False
    assert summary["n_combinations_evaluated"] == len(result.logits)


def test_cscv_pbo_counts_every_split_when_is_winner_collapses_oos():
    # Deterministic polarity probe. With A strong on partitions {0,1} and B
    # strong on {2,3}, every one of the six symmetric splits selects an
    # in-sample winner whose out-of-sample performance ties-or-loses, so
    # omega <= 0.5 in all six and PBO must be exactly 1.
    matrix = {
        "A": np.array([10.0, 10.0, 0.0, 0.0]),
        "B": np.array([0.0, 0.0, 10.0, 10.0]),
    }
    result = cscv_pbo_analysis(matrix)
    assert result.probability_of_backtest_overfitting == pytest.approx(1.0)
    assert result.n_combinations_evaluated == 6
    summary = pbo_summary(result)
    assert summary["overfit_flag"] is True


def test_cscv_pbo_zero_when_selection_persists_out_of_sample():
    # Dominant config wins in-sample AND out-of-sample in every split: no
    # overfitting signal at all.
    matrix = {
        "dominant": np.array([1.0, 1.0, 1.0, 1.0]),
        "weak": np.array([0.0, 0.0, 0.0, 0.0]),
    }
    result = cscv_pbo_analysis(matrix)
    assert result.probability_of_backtest_overfitting == pytest.approx(0.0)
    summary = pbo_summary(result)
    assert summary["overfit_flag"] is False


def test_cscv_pbo_validates_inputs_and_supports_sampling():
    with pytest.raises(ValueError):
        cscv_pbo_analysis({})
    bad = {"a": np.array([0.1, np.nan, 0.2, 0.1])}
    with pytest.raises(ValueError):
        cscv_pbo_analysis(bad)
    ragged = {"a": np.ones(6), "b": np.ones(5)}
    with pytest.raises(ValueError):
        cscv_pbo_analysis(ragged)

    big = _noise_matrix(6, 24, seed=5)
    sampled = cscv_pbo_analysis(big, max_combinations=50, rng_seed=5)
    again = cscv_pbo_analysis(big, max_combinations=50, rng_seed=5)
    assert sampled.n_combinations_evaluated == 50
    assert sampled.probability_of_backtest_overfitting == (
        again.probability_of_backtest_overfitting
    )
