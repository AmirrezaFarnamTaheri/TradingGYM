# ADR-053 — Point-in-time funding and method-level donor convergence

**Date:** 2026-10-16
**Status:** added on draft PR #2; research-only; exact-head CI and broker
qualification are **not verified**. Funded-live trading remains **NO-GO**.

## Evidence boundary: 17 new donor ZIP archives

An additional archive-wide static pass enumerated **4,226 file entries**,
**3,113 source files**, **27,328 Python function declarations**, and the
full observed nested-folder hierarchy. Python function bodies were normalized
by AST (excluding short routines, tests, docstrings, and no-op protocol
stubs), then compared across donors. The pass found **zero exact nontrivial
cross-archive Python function-body duplicates**. That result only speaks
to strict AST equivalence; it does **not** imply no synonymous, overlapping
or superior peer implementation exists.

A bounded source search surfaced 57 **manual triage markers**, including
14 shift(-N) occurrences, 3 backfill occurrences, one TLS-verification
disablement expression, one shell execution expression, and an explicit
future-funding fallback in Jeeva's Rust backtest helper. These are
**not 57 confirmed bugs**: label construction, tests and intentionally
sandboxed subprocesses can legitimately match those patterns. Never merge
a donor module solely because a marker or symbol name matched.

The standalone analysis and per-archive source references are recorded
in the companion conversation artifacts
`semantic_source_crosswalk.json` and `semantic_source_audit.md`.
No donor code was executed during archive enumeration.

## Verified donor discrepancy: future funding data

Jeeva's `engine/src/backtest/funding.rs` function `rate_at` resolves the
latest rate at or before an as-of time, **or substitutes the first available
record when no earlier rate exists**. For a historical replay that predates
the first observation, that fallback can introduce lookahead bias.

TradingGYM now has a separate read-only
`trading_gym/risk/funding_history.py` with these contracts:

- `FundingObservation` carries **effective_at** and **observed_at** as
  separate timezone-aware times, a signed finite Decimal rate,
  instrument identity and lowercase source SHA-256.
- `resolve_funding_history` permits a rate only when **both** timestamps
  are no later than the historical as-of. Future-only data results in
  `status=unavailable`, not a substituted rate.
- Evidence records must be strictly increasing by effective time; duplicate
  or mixed-instrument tapes are rejected. Stale observations return no
  usable rate; canonical receipt hashes contain the eligible history
  prefix, excluding unseen future values.
- `funding_cashflow_estimate` is research-only; a positive rate costs a
  long and benefits a short. It **cannot extrapolate a single observed
  rate over several unobserved future intervals**. No account, settlement,
  capital or execution mutation is possible from this module.

This component is distinct from
`trading_gym/accounting/funding_reconciliation.py`, which compares
independently supplied funding dues and settlement readbacks. The new
rate-history resolver does **not** replace that reconciliation, assert a
funding obligation has settled, or authenticate the supplied SHA hash.

### Test evidence

The isolated standard-library Python implementation passed **18 focused
funding tests** including observed/effective time eligibility, future
prefix invariance, duplicate chronology, instrument isolation, stale data,
long/short cashflow signs, UTF offset canonicalization, malformed input,
and single-interval enforcement. In the same isolated harness, an
independent research-comparison prototype passed 15 tests: **33/33 total**.
This prototype was **not** imported into the PR because PR #2 already
contains a larger, tested paired-strategy comparison module. Avoiding that
duplicate was itself a convergence decision. The 33 tests do not
constitute a full TradingGYM integration or CI run.

## Second-order structural decisions

1. **One as-of funding evidence owner:** this new read-only time filter
   supplies a historical eligibility result. Actual funding payment
   reconciliation remains owned by accounting; venue-specific live
   settlement still requires broker readback and independent grants.
2. **One strategy comparison owner:** retain existing
   `experiments/peer_strategy_comparison.py` and its paired bar and
   seed/regime scoreboards. Do not import AI-Trader leaderboards or a
   competing research engine. Independent reviewers must validate
   search-family completeness, outcome availability and scenario
   independence before using any nominal statistical significance.
3. **One portfolio optimization owner:** existing
   `institutional_os/turnover_aware.py` already contains L1-turnover
   utility optimization and group caps. Treat Vibe's optimizer as a
   parity-fixture donor, not a second runtime optimizer.
4. **One execution control owner:** existing
   `convergence/peer_execution_parity.py` and venue/accounting controls
   own paper fill, lot/tick and broker comparison. Jev-trade/Jeeva
   can contribute independent fixture cases, not broker access.
5. **Quarantine agent executables:** donor skill routing, Hono gateways,
   Codex/browser tools and user-code execution stay outside the
   funded trading worker. The existing digest-pinned capability
   preview is the non-authoritative selection surface.
6. **Distinct UI evidence states:** dashboards must separate simulated
   actions, paper acknowledgments, independently reconciled fills and
   funded-live eligibility; a strategy win-rate or model confidence
   cannot silently upgrade one status to another.

## Release and coverage limitations

Exhaustive file and symbol indexing is not the same as hand-verifying
every method across the 17 archives. Large Vibe-Trading and Jeeva engine
families still require direct parity fixtures for corporate actions,
market sessions, venue funding, margin/settlement, position readback,
cancellation and non-atomic fills. The user-managed licensing assumption
does not itself establish upstream permissions.

At the most recently inspected GitHub Actions run, nine required jobs
were reported failed with **zero executed steps**, consistent with a
runner-allocation or pre-execution block; this is **not evidence that
the new code's tests ran and failed**. Exact-PR-head lint/format,
source-accountability generator, OpenAPI, Python 3.11/3.12, frontend,
Rust and broker qualification remain independent release gates.

**No claim of complete semantic convergence or funded-live readiness is
made. Draft PR #2 stays unmerged.**
