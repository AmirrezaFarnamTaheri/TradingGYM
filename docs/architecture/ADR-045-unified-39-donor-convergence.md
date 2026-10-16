# ADR-045 — Unified 39-archive evidence and higher-order convergence

**Date:** 2026-10-15  
**State:** complete file/folder/hash audit, partial source-level semantic review,
bounded strategy/research implementations on draft PR #2.
**Trading authority:** unchanged; funded-live NO-GO.

## Cross-batch reconciliation

Previous ten-archive, twelve-archive and seventeen-archive investigations
were separate. A new read-only scan of **all 39 ZIPs** now establishes
a single content-evidence baseline:

| Metric | Observed |
| --- | ---: |
| Uploaded archives | 39 |
| Archived file entries | 12,473 |
| Nested explicit or inferred ZIP directory paths | 2,238 |
| Distinct SHA-256 file payloads | 11,802 |
| Cross-archive same-content hash groups | 304 |
| Exact archive-content duplicate pairs | 3 |
| Distinct archive content multisets | 36 |

Three exact duplicates: `jev-ultrafast-main.zip` and its `(1)`
upload; `Jev-Auto-Router-master(1).zip` and `(3).zip`;
`JevLoop-main.zip` and `JevLoop-main(3).zip`.
The SHA-256 scan read every archive member in bounded chunks;
it did **not** extract or execute installers, agents, brokers, terminals,
dependencies, or large data files. All paths, member sizes and payload
hashes are retained in the attached machine-readable audit; all nested
folders, including empty ZIP directory entries, are in its folder register.
The 304 same-content groups include boilerplate, empty files and fixtures,
so they do **not** represent 304 proved reusable or superseding modules.

**Completeness boundary:** file/folder inspection and reproducible hashes
are exhaustive for these uploads. Method-by-method semantic comparison
of 12,473 files and proof of behavior/fitness is **not complete**.
Neither a shared symbol name nor an identical helper file establishes
compatibility across source languages, dependency graphs, licenses, data
models, concurrency, or trading authority.

## Implemented convergence in the current branch

- **Method plane:** causal Wilder ADX / positive/negative directional
  indicators and research presets; chronological candle validation;
  flat-price RSI neutral behavior and configurable stochastic `%D`
  warmup, with observed regressions.
- **Research / experiment plane:** sealed, paired strategy holdout
  comparator using aligned as-of bars, bounded fee and slippage
  assumptions, deterministic report receipts, descriptive block
  resampling and explicit non-promotable status. Bounded,
  secret-safe read-only CLI.
- **Second-order cost-sensitivity plane:** 2–16 fee/slippage scenarios
  reuse the same paired holdout and expose ranking reversals without
  claiming real fills or executing strategies.
- **Existing infrastructure preserved:** native typed System-One and
  chat-completions adapters, credential-request admission, model
  attribution, portfolio rebalance and risk-policy ownership,
  venue/broker permission gates, and frontend operator governance
  remain single-instance components, not duplicate donor runtimes.

The local Python 3.13.5 source mirror passed **55 focused tests** across
the paired scorecard, cost sensitivity, indicator fixes, ADX and
chronological validation. The local mirror is not the full repository
and cannot certify Python 3.11/3.12 dependency parity, Black/Ruff,
Pydantic/FastAPI integration, React/Rust, or broker settlement.

## Second-order product-plane convergence priorities

**P0: Multi-venue as-of backtesting and market evidence.**
Vibe-Trading contains multiple venue-specific backtest engines and
data loaders. Compare session calendars, corporate actions, exchange
timestamps, funding, borrow, dividend and fee conventions against
TradingGYM's existing `market/data_router.py`,
`market/catalog.py`, strategy lab and purged walk-forward tests.
Required acceptance evidence: sealed market snapshots and pairwise
equivalent results on common fixtures, including missing/stale and
late-arriving bars. No automatic second engine import.

**P0: Execution reconciliation and capital isolation.**
The jeeva funding/session/position system, Jev Trades paper-order loop,
and jev-trade sleeve/IOC/maker mechanism should be compared against
TradingGYM's `risk/derivatives.py`, `portfolio/rebalance.py`,
venue routing and account reconciliation. Compare tick rounding, fee
currency, funding signs, partial fills, order cancellation races,
idempotency, isolation between sleeves and emergency gross-flat
readback. Before actual integration, keep all donor credentials disabled.
Dry-run correctness does not grant funded-live authority.

**P1: Cost/turnover-aware portfolio optimization.**
Vibe's optimizer has explicit causality and constrained-turnover tests.
TradingGYM already owns `maximum_turnover_fraction` and
`estimated_cost_bps` on dry-run rebalance plans, so importing another
rebalance implementation would duplicate authority. A useful future
improvement is an offline cost-aware transition objective using the
existing portfolio-construction ownership, with constraints proven
feasible and objective sensitivity separately evaluated.

**P1: Multi-strategy experiments and trustworthy rankings.**
AI-Trader's experiment ledger/agent scores can inform audit UX, but
social activity, agent reputation, and uncalibrated LLM confidence must
not be confused with out-of-sample market alpha. The new paired
research evidence and cost-sensitivity surfaces are the first port.
Further work requires independent walk-forward regimes, explicit
data-availability times, measured executable fees/fills, selection
multiplicity, seed/rerun receipts and role-scoped comparisons.

**P1: Agent skills, tools and typed provider fleet.**
Skillbox and type-safe model/skill routers contribute versioned
manifests and bounded capability eligibility. TradingGYM already
has a governed provider fleet, key rotation, request ledger, native
System-One, working-memory consent and source graph review.
Port only proof-backed manifest/version/permission checks at that
existing boundary. No invisible background tool execution, opaque
terminal browser, or arbitrary scripts in funded-trading workers.

**P2: Operator UX convergence.**
Vibe/jeeva/AI-Trader dashboards can inspire account provenance,
fee-impact graphs, multi-strategy comparison and replay diffs, but
integration should avoid duplicate nav trees and must label paper,
simulated, delayed and broker-reconciled states separately. Add widgets
only after authenticated API contracts and actual screenshot/regression
evidence are available.

## Outstanding independent release gates

Full exact-PR-head CI has not executed in the observed GitHub Actions
record for the latest commits. Run the repository-wide Python
3.11/3.12 matrix, Black/Ruff/Mypy, source-accountability and OpenAPI
regeneration checks, frontend build/Vitest, Rust validation, actual
System-One checkpoint protocol tests, and broker fault-injection and
settlement readback. Verify current PR mergeability on GitHub (which
can initially report an unknown mergeability state).

**Disposition:** PR #2 remains draft, unmerged, and funded-live **NO-GO**.
Even a successful release check would not, by itself, prove exhaustive
semantic absorption of every donor method or waive intellectual-property
review; the user's MIT assumption is treated as a planning preference,
not evidence of every donor's license.
