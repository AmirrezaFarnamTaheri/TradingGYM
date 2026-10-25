> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-047 — ADV capacity evidence and unified paired research comparisons

**Date:** 2026-10-15  
**Target:** [TradingGYM PR #2](https://github.com/AmirrezaFarnamTaheri/TradingGYM/pull/2)  
**State:** implemented on draft branch; exact-head CI and broker qualification incomplete  
**Authority:** read-only, no strategy promotion, automatic selection, brokerage or funded-live orders.

## Source-driven convergence and ownership

Two donor behaviors were valuable at a larger architectural level than
individual indicators:

1. **Vibe-Trading / `agent/backtest/factor_costs.py::apply_adv_capacity`**
   conservatively treats missing/zero average daily traded *value* (ADV) as
   untradeable. Its original behavior clips portfolio weights. TradingGYM
   has an existing, governed `portfolio/rebalance.py` plan that already
   computes quantity increments, fees, reserve cash, residuals and blockers.
   Silently clipping an order after planning would make that plan's cash,
   fees and risk projections stale.
2. **AI-Trader / `service/server/experiment_metrics.py`** uses
   model-performance scorecards. Combined with Vibe-Trading's cost/turnover
   accounting, scenario-level paired, explicitly cost-adjusted comparisons
   add different evidence from a single bar-replay average. TradingGYM
   already had `experiments/peer_strategy_comparison.py` implementing
   bar-level paired replays and descriptive block intervals, plus a
   `cost_sensitivity.py` grid and bounded CLI. A second independent
   bar-replay engine would violate single-owner convergence.

**Ownership resolution:** `portfolio/capacity.py` handles portfolio
liquidity evidence (distinct from `operations/capacity.py`, which
models *worker-pool* throughput). The scenario-level Holm/sign diagnostic
was folded into the existing
`experiments/peer_strategy_comparison.py`, and the provisional duplicate
`experiments/paired_research.py` was deleted. Both research grain
types share one canonical source module, but retain distinct input
contracts and explicit labels to prevent invalid comparisons.

## Read-only ADV/participation assessment

`portfolio/capacity.py` adds
`AdvSnapshot(instrument_id, traded_value_base, observed_at_unix,
source_sha256, base_currency)` and
`assess_rebalance_capacity(plan, observations, as_of_unix, ...)`.
Both are exported by the existing `trading_gym.execution.portfolio` package.

- The original `RebalancePlan` is never rewritten, clipped, repriced,
  resettled, or granted execution authority.
- Market volume is a **value** amount, not shares. `Decimal` arithmetic
  and explicit base-currency matching prevent false capacity from unit
  mismatch. Negative, NaN, infinite, oversized, duplicate, malformed
  digest or unrelated observations are rejected.
- Missing, zero, stale (default >24h) and future evidence blocks the
  proposed trade instead of treating unknown ADV as unlimited liquidity.
  A plan that was previously infeasible remains infeasible.
- Maximum participation defaults to 5% and is bounded in (0,1].
  `capacity_notional_base`, per-instrument participation and shortfall
  are surfaced in a deterministic report. The original cost/cash plan
  is not modified, so residual capacity requires a **fresh plan** to
  recompute cash, leverage, costs and potential partial fills.
- Evidence source hashes and the original rebalance plan hash are
  bound into the report SHA-256. Those values detect changes to
  operator-supplied evidence, but do not authenticate venue provenance.
- `advisory_only=true`,
  `execution_authority_granted=false` are invariant.

The isolated implementation fixture established 12 initially failing
tests against a placeholder and 12 passing after implementation.
A follow-up currency-mismatch test was RED then GREEN. The final
source-matched isolated suite passed **13/13**. A repository-level
`RebalancePlan` fixture was added to
`tests/portfolio/test_capacity_evidence.py`; that exact-head
integration test has not run under the pinned TradingGYM environment.

## Unified scenario-level paired research evaluation

`experiments/peer_strategy_comparison.py` now exposes two distinct
non-authoritative analyses:

| Grain | API | Evidence |
| --- | --- | --- |
| Per-bar replay | `compare_strategy_replays` | Strictly aligned closed-bar snapshots, estimated fees/slippage, signed turnovers, compounded net returns and *descriptive* moving-block intervals |
| Seed/regime scenario | `compare_paired_research` | Independently labeled holdout scenarios, paired tape/seed/regime/time evidence, explicit gross minus cost fraction, one-sided exact sign test and Holm-Bonferroni adjustment |

The scenario-level `ResearchRun` contains strategy ID, seed,
regime, common source-tape digest, run-source digest, start/end/label
availability timestamps, realized gross return, explicit cost fraction,
and `split="holdout"`.

The comparison refuses duplicate/missing candidate scenarios, mismatched
tape/windows, negative or missing costs, nonfinite results, train-split
data, label availability after the requested as-of time, malformed
source hashes, and empty/inadequate regime evidence. It caps each
candidate at 256 scenarios and the model roster at 32.

For each candidate versus a fixed baseline, a one-sided binomial sign
tail counts paired improvements net of declared costs, excluding ties
from the informative sample. Holm-Bonferroni corrects nominal p-values
over the pre-supplied model roster. Exact p-values assume exchangeable,
independent paired challenges under the null; **that assumption has not
been independently checked**. These statistics are not claims of causal
alpha, calibrated returns or permission to rank automatically. All
reports retain `statistical_independence_verified=false`,
`selection_authority_granted=false`,
`promotion_authority_granted=false`, and
`funded_live_eligible=false`.

A 16-case RED-to-GREEN suite passed against the tested standalone
implementation; the function body was then transferred unchanged
into the existing comparison module. The new
`tests/experiments/test_paired_research.py` imports the canonical
module and must pass with the previous bar-replay regression suite
before release.

## Second-order convergence implications

- **Capacity joins the portfolio risk plane** as evidence, not another
  allocator. Future integration should require sealed ADV acquisition
  and portfolio-level buy/sell cash, fee, market-impact and settlement
  reconciliation before any feasible plan is promoted.
- **Statistics join the research plane** as paired experiments, not another
  agent selector or source of executable trading calls. Bar-level
  descriptive intervals and scenario-level multiplicity-adjusted
  p-values must remain separately labeled.
- **Evidence provenance** requires observations witnessed by the relevant
  market/venue, custody of exchange timestamps, canonical trade
  records, actual costs and seed/regime holdout independence. Hash
  commitments alone are not independent verification.
- **UI and API integration** must display explanatory blockers and
  non-authoritative results, never trade buttons or green live-ready
  badges based solely on these reports.
- **Licensing** remains subject to actual per-donor source license
  verification; user-supplied "assume MIT" does not itself change
  upstream legal grants.

## Verification and outstanding gates

- Local isolated Python 3.13 suite: **29 passing** across ADV capacity
  and paired scenarios; syntax compilation succeeded.
- The tested production functions were source-SHA verified on GitHub,
  then consolidated under canonical owners; final PR-head integration
  and style checks remain outstanding.
- Separate existing PR changes include post-fee equity correction,
  deterministic strategy comparisons and cost sensitivity, native
  System-One contracts and market guards. No duplicate runner,
  broker or model gateway was imported here.
- Required exact-head tests: complete Python 3.11/3.12 matrix, Black/Ruff,
  module-accountability `--check`, FastAPI OpenAPI `--check`, React,
  Rust, real exchange/venue fault injection, settlement and emergency
  gross-flat verification. CodeRabbit success is not a substitute.
- GitHub Actions runner allocation has been exhausted in prior runs;
  the latest PR head still lacks a successful full CI execution.
- Branch stays **draft**, **unmerged**, and funded-live remains **NO-GO**.
