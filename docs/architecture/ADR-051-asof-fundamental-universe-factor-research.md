> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-051 — Point-in-time fundamentals, historical universe and factor research admission

**Date:** 2026-10-16  
**Target:** TradingGYM draft PR #2 (`audit/2026-10-11-execution-ci-hardening`)  
**Disposition:** new research-only implementation; no funded execution, provider calls, factor activation or portfolio mutation.

## 1. Donor coverage and selected supersessions

The 17 newly supplied archives contain **4,226 files**, **3,124 source-classified files** (including tests and bundled examples), **619 nested directory paths**, and **4,180 distinct SHA-256 content hashes**. Every file was inventoried and hashed without executing archive code; a separate source pass extracted **44,100 AST/heuristic declarations**, with **974 names** appearing in more than one archive. These names are *candidate comparisons*, not semantic equivalence or superiority proofs.

The files `jev-ultrafast-main(1).zip`, `JevLoop-main(3).zip`, and `Jev-Auto-Router-master(3).zip` are each byte-identical to their respective earlier uploaded archives. Re-importing their executable runtimes would add duplication without new functionality.

| Donor archive / architecture | Relevance and current disposition |
| --- | --- |
| `Vibe-Trading-main.zip` | Fundamental SEC loader (`agent/backtest/loaders/fundamentals_loader.py`) explicitly anchors data on filing dates instead of fiscal period ends; `_fundamental_schema.py` separates annual, quarterly and year-over-year dependencies. Its survivorship regression (`agent/tests/test_alpha_bench_meta_survivorship.py`) requires historical index-source bias disclosure in both JSON and HTML. **Adapted** into two strict read-only target contracts rather than copying the market-specific loader and its live dependencies |
| `AI-Trader-main.zip` | Research challenge exports, experiment labels and measured agent performance; complements TradingGYM's existing paired statistical research, without qualifying results for live trading |
| `claude-trader-main.zip` | Coinbase/Kraken strategies, regime scores and execution-review workflows; indicators/strategy ideas are already selectively represented in Strategy Lab, not copied as a second live agent |
| `jeeva-main.zip` | Rust perpetual futures decisions, funding/reconciliation, typed live order attempts. TradingGYM already owns separate portfolio/execution/corporate-action contracts; venue-specific settlement evidence remains a release gate |
| `Jev-Trades-master.zip` and `jev-trade-main.zip` | Paper/spot and Hyperliquid books, fills, position sleeves and dashboards; comparative fixtures are useful, but neither may create a parallel capital owner |
| `tinyfinance-main.zip` | Rust template/event-bus crates, not an independently qualified portfolio accounting system |
| `jev-tree-main(2).zip` | Bounded hierarchical classifier; TradingGYM already has the research-only factor taxonomy and source hashes described in ADR-045 |
| `typesafe-jev-workflow-main(1).zip`, `skillbox-main(2).zip`, `typesafe-skill-router-main(3).zip` | Type-specified workflow and scoped skill catalogues; no dynamic import, shell access, MCP execution or model-suggested grant added to the trading runtime |
| `JevRouter-main(3).zip`, `hono-jev-router-main(3).zip`, `pi-jev-router-main(1).zip`, `Jev-Auto-Router-master(3).zip` | Routing and policy-guard ideas; preserve the current provider-admission, fallback and execution-authorization boundaries |
| `JevLoop-main(3).zip` and `jev-ultrafast-main(1).zip` | Bounded calls and speculative low-latency agent work; no speculative financial trades, network actions or unverified context pruning |

A SHA-256 of a user-supplied file is a reproducibility reference, **not** an attestation of a real exchange, regulator, issuer, SEC filing or legal license. User-provided assumptions do not relicense third-party source; this change is an independent, narrow implementation, not verbatim donor code.

## 2. First-order implementation: canonical historical filing contract

New `trading_gym.market.fundamental_asof` exports:

- `FundamentalFact`: instrument, explicitly named financial field, **annual / quarter / instant** fiscal frame, period start/end, filed time, actually observed time, exact Decimal value, explicit unit and source SHA-256
- `FundamentalQuery`: instrument + field + fiscal frame + unit; no guessed FX or share/currency conversion
- `assess_fundamentals_asof(...)`: bounded, deterministic, hash-anchored selection under an explicit historical `as_of_unix`

It validates the full observation chronology, bounded input and Decimal magnitudes, exact units, source evidence shape, and distinct fiscal annual/quarter/instant durations (to avoid mixing a true-quarter fact with year-to-date or annual aggregates). An observation becomes visible only after **both** filing and observation timestamps have passed. Among visible versions, the latest fiscal period wins, with later refilings for that period available only after their new observation. Future records may exist in a larger historical dataset but cannot change the earlier result **or its report/input digest**.

Unknown, non-current or wrong-unit facts do not become zero or fabricated fallback values. A default 550-day maximum fiscal age prevents unlimited forward fill. No TTM is synthesized from mixed-duration facts; that requires a separately audited, field-aware quarterly/YTD normalization and calendar policy.

The optional operator CLI `scripts/assess_fundamentals_asof.py` reads bounded JSON arrays with **decimal-string** values, rejects duplicate properties, nonstandard numeric constants, symlink inputs and unexpected fields, and reports sanitized errors without echoing source secrets.

## 3. Second-order implementation: historical universe membership

The canonical instrument registry's `active` flag represents current state. Using it as membership at a historical decision time introduces survivorship bias.

New `trading_gym.market.universe_asof` exports:

- `UniverseMembershipEvent`: index/universe ID, canonical instrument, effective time, publication time, observation time, explicit inclusion/exclusion and source digest
- `assess_universe_asof(...)`: a typed, bounded historical constituent report

An event is eligible only after its effective **and** published/observed dates. A newly announced future inclusion cannot be used early; an older but later-disclosed correction cannot override a more recent effective membership state. Corrections to the same effective event apply only after they become known. Exact-time conflicting observations are rejected, rather than silently selected. Unknown membership remains explicitly **unknown**, not a synthetic exclusion or assumption that current constituents existed historically.

The output never claims an independently validated survivorship-free universe merely because an index-source hash exists.

## 4. Higher-level convergence: joined factor data admission

New `trading_gym.market.factor_research_readiness` composes the two contracts at the **same** as-of timestamp.

A requested instrument must have an as-of visible, **included** universe membership, and every declared financial field must have a valid as-of filing within its freshness window. Unknown or explicitly excluded members, missing/restated-later facts, inconsistent frames, stale fiscal periods, and cross-universe query attempts deny data readiness.

The result contains blockers, separate input-plane digests, a combined report digest and explicit permanent flags:

- `factor_authority_granted=false`
- `execution_authority_granted=false`
- `source_authenticity_verified=false` / `survivorship_bias_not_independently_ruled_out=true`

`ready_for_research=true` means only that **the caller-provided records satisfy these data-shape and as-of constraints**. It does **not** establish data-provider authenticity, factor alpha, risk model calibration, safe leverage or trading authorization. These modules are exported through one canonical `trading_gym.market` API and do not mutate any registry, strategy, research database or broker.

## 5. Verification evidence and limitations

Actual source-mirror RED-to-GREEN work used Python 3.13.5 and pytest with no network calls:

| Focus | Observed isolated result |
| --- | --- |
| PIT filing/revision selection, numeric/unit/frame validation and future-data prefix invariance | 29 passing |
| Bounded CLI behavior and secret-safe denial | 4 passing |
| Historical universe membership, later corrections and unknown constituents | 18 passing |
| Joined factor research-admission and authorization boundaries | 5 passing |
| **Combined isolated total** | **56 passing** |

These are isolated pure-source tests; **exact PR-head repository tests remain unverified**, including the additional public-package export test. Final production requirements still include Python 3.11/3.12, full pytest, Ruff/Black, source-accountability `--check`, generated OpenAPI `--check`, frontend, Rust, broker and venue settlement/recovery suites. Hosted Actions runners have recently failed required jobs with zero executed steps.

The observed branch module-accountability manifest now includes the three new canonical modules and refreshed public `market/__init__.py` fingerprint. A complete full-source hash verification still needs the exact checkout generator.

### Decimal precision follow-up

The original normalizer used Decimal.__abs__ and Decimal.normalize with
the ambient thread-local context. With Python's default 28-digit precision
(or a caller-defined lower precision), a high-precision financial filing
could silently lose significant digits, altering as-of reports and
content-hash receipts. A RED regression reproduced this behavior.
The contract now bounds significant decimal digits (64) and scale
(-36..36), uses context-independent copy_abs for magnitude checks,
and normalizes with an explicit 64-digit context. Negative/positive
zero are normalized to the same canonical "0". The full isolated
suite passed **56/56**, including three new numeric edge cases.
No separate currency precision/rounding policy is inferred.

## 6. Remaining second- and third-order convergence candidates

1. **As-of research data compiler:** bind issuer identities, actual SEC filing accessions/forms, published and observed timestamps, currency units, audited security-master history and source snapshots. Reject vendor mapping aliases that collapse distinct report frames.
2. **Fundamental derivation plane:** field-aware trailing-twelve-month accumulation with true-quarter versus YTD frame handling and restatement replay; audited concept aliases rather than implicit zero fills.
3. **Corporate-action/FX plane:** reuse existing `trading_gym.execution.accounting.corporate_actions`, `market.calendar`, portfolio and derivatives contracts for historically effective splits, delistings, dividends, FX and funding. Do not copy a second execution/account ledger.
4. **Factor research UI:** show fiscal age, document/version, universe-member state, survivorship source and explicit blockers for human review, separate from any live order interface.
5. **Independent statistical admission:** combine point-in-time inputs with purged walk-forward folds, realistic spreads, turnover, ADV, fees, multiple-testing corrections and held-out comparison; prohibit same-model self-certification.
6. **Production release evidence:** exact-Git-SHA CI, provider fixtures, source manifest, comprehensive broker readback/reconciliation and independently witnessed emergency gross-flat. The research-ready flag may never cross this authority boundary.

**PR remains draft and unmerged. Funded-live remains NO-GO.**
