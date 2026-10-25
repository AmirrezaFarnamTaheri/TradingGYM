> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-044 — Bounded portfolio risk, empirical tail losses, and insolvency evidence

**Date:** 2026-10-16 · **Status:** implemented on draft PR #2; release qualification remains incomplete.  
**Target:** `audit/2026-10-11-execution-ci-hardening`  
**Source influence:** Vibe-Trading risk-xray return hygiene, historical VaR/expected
shortfall and finite/explicit data-availability conventions, adapted to the
existing TradingGYM risk modules without importing the donor runtime.

## Root causes

An isolated exact-source mirror reproduced three consequential numerical
integrity errors in the existing risk plane.

1. `risk/analytics.py` converted arbitrary return observations using
   `float(x)` and could treat NaN, Infinity, booleans, numeric strings, or
   unbounded input generators as risk evidence. It provided only
   moment-model VaR with no expected-shortfall or tail-sample assessment.
2. `risk/portfolio.py` validated position-notional finiteness but allowed
   Pydantic to accept infinite account equity/peak/day-start equity,
   implausibly large notionals and boolean risk settings. This could make
   leverage or drawdown fractions zero, NaN, or otherwise misleading.
3. `risk/stress.py` used `float('inf')` for insolvency and near-zero-equity
   leverage, could produce infinite loss fractions, and hashed a raw payload
   before Pydantic normalized its numeric types. A wire-level serialized
   report could therefore disagree with the reported SHA-256 commitment.

## Implemented contracts

### One-dimensional tail risk

`tail_risk(returns, confidence=0.99, method=...)` retains the existing
Gaussian and Cornish–Fisher method names and now adds `historical`.
Accepted observation series are bounded to 100,000 finite numerical
fractional returns, with strict confidence, provenance and method
validation. No value is dropped, forward-filled or silently coerced.

For historical observations, VaR uses the empirical loss-tail boundary and
Expected Shortfall weights a fractional final tail observation. The
`effective_tail_observations` and `insufficient_tail_observations`
warning expose underpowered high-confidence estimates. Gaussian ES is
computed from the conditional left tail of the normal approximation. The
Cornish–Fisher method computes a corrected quantile, **not a claimed
parametric ES** (`expected_shortfall=null`), while the empirical ES is
reported separately. Assumptions, approximation caveats and
`authority_granted=false` remain explicit.

These are descriptions of sampled distributional loss, not certified
probabilities of future losses or calibrated risk permissions. The
historical method assumes observed returns are relevant; correlated,
stale and censored input series remain an upstream data-quality issue.

### Portfolio state and policy

`PortfolioSnapshot` now requires finite, non-boolean account values in
`[1e-12, 1e30]`. Position notionals must be finite, non-boolean and within
`[-1e30, 1e30]`; legitimate short notionals remain supported.
PortfolioRiskLimits fields also reject booleans rather than allowing
true/false to be coerced into numeric risk thresholds. These bounds keep
worst-case finite aggregate exposure arithmetic within a representable
floating-point range for the existing 10,000-position input ceiling.

Explicit Pydantic `minimum`/`maximum` schema bounds and the
corresponding OpenAPI `PortfolioSnapshot` and `PositionExposure` fields
were updated together. The existing conservative gross-instrument and
sector aggregation and loss gates are preserved.

### Insolvent stress evaluation

`PortfolioStressReport.post_stress_gross_leverage` and
`loss_fraction` are now nullable to represent ratios that genuinely
cannot be computed. Such conditions append explicit
`gross_leverage_unavailable`/`loss_fraction_unavailable` violations and
always prevent the report from being allowed. Positive finite scenarios
still return ordinary numeric metrics; their existing leverage/loss
policy comparisons remain intact.

Equity numeric input is checked before conversion, booleans are rejected,
and canonical payload hashing uses finite JSON and the same normalized
floating-point equity representation serialized by Pydantic. The published
OpenAPI fields and regression tests express the nullable contract. No
Infinity/NaN token is intentionally emitted in a risk report or hash
preimage.

A report describes a stress outcome only; its rejection and receipt are
not commands to flatten, cover or rebalance a live venue position.

## Verification and residual concerns

Recorded **RED -> GREEN** local Python 3.13.5 cycles:

- Tail-risk: 13 failures in the original method and 5 preexisting passing
  focused cases (excluding the original endless-generator hang). After
  bounded materialization and method corrections: **19/19 passing**.
- Portfolio input: 19 cases failed on non-finite/boolean coercion,
  followed by **23/23 passing** focused portfolio cases.
- Stress outputs: insolvency, near-zero leverage, canonical hash and
  extreme loss cases failed before repair; **5/5 passing** after.
- OpenAPI source contracts: numeric account/notional bounds and nullable
  stress ratios failed old snapshots and passed after targeted schema edits.
- Consolidated isolated regression run: **49/49 passing**, plus successful
  Python source compilation. These tests use a targeted module mirror,
  **not** the full PR-head checkout.

Independent GitHub inspection after commits found **360/360** Python
source paths in the module-accountability manifest, zero missing/extra
paths, and exact matching SHA-256 fingerprints for the three changed risk
modules. The relevant published schema properties were also inspected
against their intended Pydantic shape.

**Unverified:** application-derived full OpenAPI generator diff;
Python 3.11/3.12 complete suite; frontend and Rust builds; external broker
and venue fault injection; as-of market-data feed fidelity; portfolio risk
X-ray over correlated asset returns; independent stress and ES backtests;
and same-head GitHub Actions. None of these gates is marked green by an
isolated Python mirror.

## Second-order architectural convergence

The next major product boundary is a single **Risk Evidence Plane**:
correlated portfolio return tapes, event-time provenance, missing-return
policy, empirical and parametric loss estimates, normal/filtered stress
scenarios, liquidity/carry/funding shocks and valuation/fill reconciliation,
all anchored to immutable data and contract hashes. Reuse existing
`risk/portfolio.py`, `risk/stress.py`, `risk/derivatives.py`,
`institutional_os/portfolio_construction.py` and
`experiments/peer_strategy_comparison.py`; do not create a second
capital owner or broker bridge.

A future production gate must independently verify tail calibration,
valuation timestamps, position readback, gross/net settlement and emergency
flatness. Donor risk heuristics and raw language-model conviction scores
never promote a strategy or approve live capital use.

**Disposition:** PR #2 remains draft, unmerged, and **funded-live NO-GO**.
