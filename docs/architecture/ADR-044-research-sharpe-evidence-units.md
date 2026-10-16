# ADR-044 — Correct probabilistic/deflated Sharpe evidence and research unit safety

**Date:** 2026-10-15. **Target:** existing TradingGYM PR #2,
`audit/2026-10-11-execution-ci-hardening`.
**Authority:** research evaluation only; no funded-live, order, promotion
or model-routing grant.

## Root cause: mismatched annualisation in probabilistic Sharpe

`trading_gym/strategy_lab/overfitting.py` previously computed
`sr = annualised_sharpe(values, periods_per_year)` and inserted that
**annualised** ratio into the finite-sample non-normality correction:

```text
PSR = NormalCDF(
  (SR_period - benchmark_period) * sqrt(n - 1)
  / sqrt(1 - skew * SR_period + (kurtosis - 1) * SR_period**2 / 4)
)
```

The estimator is derived with returns, Sharpe, skewness and kurtosis measured
**per sampled period**, not with annualised Sharpe. For the public
`probabilistic_sharpe_ratio` API, `benchmark_sharpe` retains the
**annualized input convention**, matching `annualised_sharpe`; the
implementation now converts the benchmark by `sqrt(periods_per_year)`
and uses `mean(returns) / sample_std(returns)` in the formula.

A synthetic nine-return fixture produced a falsely overconfident old value
of **1.0000** versus the corrected **0.83922934856077**; with a 1.2
annualised benchmark the old value was approximately **1.0000** versus
**0.779930983226804**. These are reproducible unit-test examples,
**not realized strategy results**.

A zero annualized benchmark now yields the same PSR whatever presentation
frequency is used, and using the annualized observed Sharpe as its hurdle
returns 0.5. Invalid periods (nonpositive, fractional or boolean) and
nonfinite/boolean benchmarks are rejected before statistics execute.

## Walk-forward completeness, threshold and bootstrap admission

Additional controlled RED reproductions identified that the walk-forward
summary silently discarded missing/inf train or holdout windows, changing
its sample size and its reported robustness. The implementation now requires
the complete one-to-one finite paired series. Its robustness policy also
requires a positive integer `min_windows`, finite nonnegative efficiency
minimum, finite profitable-window threshold in [0,1], and a finite median
degradation ceiling. Masked division avoids zero-baseline runtime warnings
without changing the established zero-denominator fallback semantics.

The `bootstrap_sharpe_interval` research helper also discarded NaN/Inf
observations and admitted pooled 2D candidate matrices and non-integer
annualization frequencies. It now requires bounded integer bootstrap
sample count (100–100000), finite confidence in [0.5,1), positive integer
annualization, and one complete finite numeric return vector. Valid seeded
bootstrap results remain reproducible and no donor is allowed to alter
the funded-live authority.

Isolated-source RED tests: **4 missing walk-forward windows**,
**8 malformed bootstrap inputs**, and **21 walk-forward policy/warning
cases** reproduced the original defects. After the separate fixes, the
combined focused Python 3.13 mirror passed **112/112** cases with
syntax compilation. These figures are *not exact-head full-repository
test results*, because the repository checkout could not be obtained.

## Provenance of inputs

The PSR and annualized display Sharpe inputs now require one finite,
one-dimensional numeric return sequence. Previously it removed NaN/infinity values and silently flattened
multiple strategies into a single sample. This could make statistical
confidence look stronger after ignoring missing or contaminated
observations. The function now rejects nonfinite returns, boolean
return vectors and multi-strategy arrays instead of hiding data loss.

The `annualised_sharpe` display helper was subsequently harmonized
with the same complete-evidence contract: it rejects NaN/infinite,
boolean and pooled return matrices and requires a positive integral
annualization factor. Its finite valid calculation is unchanged.

## Permutation/randomized baseline statistical admission

A companion audit found that `randomized_baseline_p_value` compared
unvalidated scores with null simulations. With an observed score of
NaN, every `score >= observed` comparison was false, creating an
artificially **minimum possible p-value** that could pass a
multiple-testing filter. Corrupted null simulations were also silently
dropped; unrelated 2D scenario arrays were pooled.

The permutation helper now requires a finite observed statistic and one
complete, finite, one-dimensional numeric vector of at least 20 null
draws. Boolean input, missing values and pooled matrices are denied.
The original Phipson-Smyth `(exceedances + 1)/(N + 1)` correction,
inclusive handling of ties, and deterministic finite-sample behavior
are preserved.

This protects statistical *input validity*. It does **not** establish
exchangeability, temporal independence, correct null generation,
strategy causality, or actual trading advantage.

## Second-order deflated Sharpe hardening

The deflated Sharpe path previously accepted NaN, infinity, boolean counts,
nonintegral trial counts, and contradictory skewness/kurtosis inputs.
The updated `sharpe_estimator_variance`, `expected_maximum_standard_normal`
and `deflated_sharpe_ratio` now validate their numeric domains,
observation/trial counts, finite variance and Pearson's sample-moment
inequality (`raw kurtosis >= 1 + skewness**2`).

**Important:** the DSR public API continues to expect the **per-period**
observed Sharpe and trial-Sharpe dispersion. It does **not** infer an
annualization frequency from its arguments, and is not interchangeable
with an annualized report without explicit conversion. Its result does
not prove an independent data tape, sufficient track length, i.i.d.
returns, strategy alpha or deployment eligibility.

## Test-first evidence

A standalone mirror of the exact original Sharpe functions was used because
a full repository checkout was unavailable in the execution container
(GitHub name resolution blocked; GitHub connector permitted targeted
read/write).

- PSR units: **9 failing** original cases / 12 passing after repair.
- DSR domains: **17 failing** original cases / 29 passing after repair.
- PSR returns integrity: **5 failing** original cases / 6 passing after
  explicit input admission.
- Permutation evidence: **9 failing** original cases / 10 passing
  after strict finite sample and observed-statistic admission.
- Final combined focused mirror: **102/102 passing** with Python 3.13.5,
  pytest 9 and NumPy 2.3.5; `compileall` succeeded.

The corresponding regression tests were committed to
`tests/convergence/test_probabilistic_sharpe_units.py`,
`tests/convergence/test_sharpe_evidence_integrity.py`, and
`tests/convergence/test_permutation_evidence_integrity.py`,
`tests/convergence/test_walk_forward_evidence_integrity.py`,
`tests/convergence/test_walk_forward_policy_integrity.py`, and
`tests/convergence/test_bootstrap_evidence_integrity.py`, and
`tests/convergence/test_annualised_sharpe_evidence_integrity.py`.
These are **not** full exact-branch test results. Real-world tape
independence, autocorrelation adjustments, fees, serial dependence,
multiple-trial recording and venue reconciliation still require their
own acceptance tests.

## Donor overlap and second-order architecture

The Vibe-Trading `backtest/optimizers/turnover_aware.py` research method
was compared with the existing TradingGYM
`institutional_os/turnover_aware.py` and
`institutional_os/portfolio_construction.py`. The latter already
implement strict finite/PSD constraints, allocation bounds,
group-cap feasibility, turnover regularization, and read-only report
identities. Replacing them with the donor optimizer would introduce
a duplicate capital-planning owner and be a regression. The donor's
signed-position/backtest-specific differences remain candidate
**parity fixtures**, not an unqualified live portfolio rewrite.

For future strategy evaluation, a high-level Research Evidence plane should
bind per-period return units, source and holdout hashes, sampling frequency,
observation coverage, serial-correlation assumptions, effective strategy
trial count, fee assumptions and PSR/DSR reports. Promotion must remain
separate from research significance and funded-live approval.

## Current PR and release status

The Git tree and module-accountability manifest each list **356** Python
application modules, with no missing or duplicated paths. The changed
`overfitting.py` SHA-256 entry was updated.

The observed GitHub CI workflow on this branch
(`38785467227`) marked nine required jobs failed **with zero executed
steps and no runner allocated**. That failure does not demonstrate that
source tests have failed, and must not be treated as a green check either.
The pinned Python 3.11/3.12, frontend, Rust, full OpenAPI/hash generator
and real broker/venue suites are outstanding. PR #2 remains **draft**
and funded-live trading remains **NO-GO**.

## Research references

- Bailey & López de Prado, *The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting and Non-Normality* (2014).
- Per-period Sharpe derivation and uncertainty:
  https://github.com/quantskills/skill-backtest-overfit/blob/main/references/methodology.md
- Formula and unit explanations:
  https://daru.finance/projects/deflated-sharpe

**No claims of financial returns or performance superiority are made.**
