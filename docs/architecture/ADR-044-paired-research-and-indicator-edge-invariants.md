# ADR-044 — Paired holdout strategy evidence and indicator edge cases

**Date:** 2026-10-15  
**Status:** integrated in draft PR #2; standalone test evidence only  
**Authority:** offline research, no brokerage/order/model-promotion access.

## Source comparison and ownership

The `AI-Trader` donor's `service/server/experiment_metrics.py` provides
agent experiment scorecards and time-window snapshots. Its community and
collaboration counters are not a substitute for measured strategy alpha.
The `Vibe-Trading` donor's
`agent/backtest/optimizers/turnover_aware.py` and
`agent/tests/test_optimizer_causality.py` provide separate references for
net-of-turnover costs and exclusion of the decision bar from optimizer
lookback. TradingGYM already owns the backtest, strategy grammar, and model
governance layers, so this change adds an **offline scorecard**, not a new
broker, trading agent, autopromotion service, optimizer runtime or dashboard
backend.

### Implemented research comparator

`trading_gym/experiments/peer_strategy_comparison.py` adds:

- `StrategyReplay`: immutable strategy identifier, canonical lowercase
  artifact SHA-256, market SHA-256, strictly ascending bar-close Unix times,
  per-bar gross return in basis points, traded-notional turnover fraction,
  and the explicitly labeled `holdout` split.
- `compare_strategy_replays(replays, *, baseline_id, train_end_unix,
  as_of_unix, fee_bps=5.0, slippage_bps=2.0)`: 2–16 distinct strategies
  and 20–5,000 common bars; denies duplicate identity/artifact, mismatched
  market, nonaligned times, a train/holdout collision, unseen future bars,
  invalid numeric data, impossible equity, cost-rate violations and
  numeric overflow.
- Per-strategy net-of-fee/slippage estimated return, compounded fractional
  return, cost bps, mean paired net bps versus a fixed baseline, and
  a deterministic **descriptive** 256-draw circular moving-block interval
  with a family-size tail adjustment. The heuristic interval is **not**
  a formal family-wise confidence guarantee and must not be used as a
  deployment/promotion threshold.
- Stable input and report SHA-256 receipts; input order cannot change the
  result. No raw per-bar returns or strategy recipes appear in the report.
  Hashes ensure reproducibility, but cannot authenticate an exchange's
  observed market or independently certify the simulated fills.
- Reports and every strategy entry unconditionally set
  `eligible_for_funded_live=false`. `selection_authority=none`.

A bounded, read-only `scripts/compare_strategy_replays.py --input
/path/to/holdout.json` accepts only an exact JSON object with
`baseline_id`, `train_end_unix`, `as_of_unix`, `fee_bps`,
`slippage_bps`, and `replays`. Each replay supplies exactly the seven
dataclass fields above. The CLI rejects symlinks, oversized files,
duplicate JSON properties, arbitrary/secret-bearing fields and invalid
records with a generic error string; it never invokes a broker or web API.
This is a reference/research entrypoint, not an exposed production
FastAPI endpoint.

No guarantee is made that turnover is the venue's actual executed
turnover, that fill costs are independently measured, or that the
strategy-generating model was trained without leakage.

### Method-level indicator defect repairs

`trading_gym/strategy_lab/indicators.py` contained two separate
correctness problems:

1. Flat OHLC closes yield zero smoothed gains *and* zero losses. The
   former implementation reported RSI=0, falsely equating an unchanged
   market with a deeply oversold regime. It now produces neutral RSI=50
   after the warmup, preserving RSI=100 on uninterrupted gains and RSI=0
   on uninterrupted losses.
2. Stochastic `%D` accepted a configurable `smooth` window but always
   requested `min_periods=3`. A two-period setting raised a Pandas
   `ValueError`, while a five-period setting published incomplete values
   two observations early. Its rolling mean now uses the actual requested
   smoothing length for both parameters.

These bugs were reproduced as RED cases against isolated copies of
the reviewed production method bodies and passed after the patches.
No claim is made that arbitrary prior backtest results are automatically
corrected; research must be replayed with the new indicator revision.

## Test evidence and trust boundary

On an isolated Python 3.13.5 source mirror:

- Original research comparator: **15 behavior tests failed** against
  `NotImplementedError`; after implementation, **19 passed**.
- Bounded CLI: initial missing-command failures; final **3 passed**.
- Numerical overflow regression: reproduced native `OverflowError`,
  then replaced it with explicit validation.
- Indicator edge cases: **3 expected RED failures** (RSI=0 on flat
  markets; two/five-bar stochastic warmup mismatch); **5 passed** after
  correction.
- With the previously mirrored ADX and timestamp integrity cases, the
  combined focused suite reported **47/47 passing**, and Python
  syntax compilation succeeded.

The four new comparison/CLI source and test files were verified
**byte-identical** on GitHub to the executed source mirror using the
canonical Git blob hash. Only the changed RSI/stochastic method bodies,
not the entire strategy module and ecosystem, were mirrored locally.
The complete repository's pinned Python 3.11/3.12 suite, formatter,
type-check, frontend/Rust builds, generated OpenAPI equality,
all-source-hash audit, and external broker settlement tests are
**unverified**.

## Higher-order convergence decisions

1. **Research evidence plane:** make strategies comparable before
   heuristics, agent rankings, portfolio blending, or model promotion;
   hash-bound as-of cohorts and cost assumptions.
2. **Market/venue causality plane:** test the entire path from candle
   observation to the next execution event, including closed bars,
   tick/lot minimums, maker/IOC fills, funding, borrow, fees and slippage.
3. **Selection multiplicity plane:** measure the true search volume,
   deflated selection statistics, non-overlapping regimes, and
   walk-forward stability. The current scorecard is descriptive only.
4. **Product experience:** an evidence-backed, permission-scoped
   comparison UI may later present the same signed statistics, without
   turning a paper leaderboard into executable strategy selection.
5. **Independent capital authority:** unchanged. Human-reviewed
   brokerage reconciliation and emergency gross-flat proofs remain
   prerequisites for funded trading.

**Disposition:** PR #2 stays draft and **funded-live NO-GO**.
No claim of exhaustive 39-archive method-level acceptance is made by this
incremental, test-backed integration.
