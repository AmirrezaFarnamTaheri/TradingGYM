> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-045: Funding readback and causal correlation-regime convergence

Date: 2026-10-15. Target: TradingGYM draft PR #2.
Authority: research and accounting evidence only; funded/live trading NO-GO.

## Source audit

The newest 17 ZIP donors were enumerated member-by-member, including
nested folders and binary payloads, without running their installers or
trading services. A full hash inventory records 4,226 files, 4,180
distinct SHA-256 payloads, eight identical-content groups, 3,124 code
files inspected for parseable symbols, 34,166 declarations and 290
cross-archive shared-name groups under the current indexing heuristic.
No ZIP read errors or Python syntax parse failures were recorded.

Three donor uploads have exact normalized member-path and content maps
matching earlier uploads: jev-ultrafast-main(1), Jev-Auto-Router-master(3)
and JevLoop-main(3). Duplicates do not justify importing new runtimes.
ADR-043 already maps every one of the 17 donor archives. Filename and
AST indexes do not establish semantic superiority or qualification.

## Implemented: signed funding settlement verification

Sources compared: jeeva-main/engine/src/funding/payment.rs,
jeeva-main/engine/src/backtest/funding.rs,
jeeva-main/api/src/funding/repository.ts and Vibe-Trading-main/
agent/backtest/{perpetual_evidence,binance_account_reconciliation,
binance_tolerance_calibration}.py.

TradingGYM's canonical accounting package now exports the read-only
FundingDue, FundingReceipt and reconcile_funding API from
trading_gym/execution/accounting/funding_reconciliation.py.

For positive funding, longs pay and shorts receive; the sign reverses
when funding turns negative. Expected settlement obligations and
independently observed cash deltas remain separate. The evaluator
requires exact account, venue, instrument, quote-currency and event
identities, strict Decimal rates and amounts, a closed as-of timestamp,
canonical source fingerprints and unique settlement receipt IDs.

Missing, extra, duplicate, premature, mismatched-scope and
amount-mismatched receipts create deterministic blockers even when
cash errors offset in aggregate. A zero-record assessment does not
claim clean settlement. Caller-supplied tolerance is strictly bounded.
Expected amounts are never automatically credited or reconciled
against an untrusted ledger. Hash-bound report fields do not attest
independent source authenticity and always report both
mutation_performed=false and funded_live_approved=false.

The donor Jeeva replay helper offers the earliest *future* funding
rate as a fallback when a replay timestamp predates the rate series.
That temporal fallback is deliberately NOT ported: TradingGYM
rejects future effective and settlement timestamps.

TDD: 30 failing tests against the unimplemented contract, followed
by 30/30 passing local Python regression cases.

## Implemented: causal multi-asset correlation regime

Sources compared: Vibe-Trading-main/agent/backtest/regime.py,
correlation.py and its undefined-correlation regressions, contrasted
with claude-trader-main/src/strategy/regime.py's uncalibrated
sentiment and position multipliers.

TradingGYM's research package now exports correlation_regimes and
classify_density_timeline from
trading_gym/agent/research/correlation_regimes.py.

The component accepts timezone-aware, strictly ordered, second-aligned
asset close-return samples, never future returns. Rolling absolute
pairwise Pearson correlations are converted to edge density only when
EVERY pair has defined evidence. Constant assets, missing observations
and missing rolling windows yield an explicit UNKNOWN state, not a
fabricated low-correlation or risk-on signal.

A strictly trailing smoothing window and two-threshold hysteresis
labels FUSED, DIVERSE or UNKNOWN. An unknown bar remains visibly unknown,
though the internal last-known state is retained for a future valid
bar. The component bounds assets, history, work units and thresholds,
and publishes input and report digests. It always denies direct trading,
calibration and portfolio mutation authority.

TDD: 20 cases failed against an unimplemented contract, then
21/21 cases passed, including future-prefix and permutation invariance.
Combined isolated local suite: 51/51 tests passed with syntax compilation.
This is not an exact full-checkout or hosted CI qualification.

## Second-order product convergence

1. **Venue-as-of evidence fabric.** Link funding, fee, fill, position and
   cash events with canonical identities and independently observed
   timestamps. Never infer a settled balance from a predicted payment.
2. **Regime-conditioned strategy evaluation.** Compare sealed, paired
   holdout returns by FUSED/DIVERSE/UNKNOWN strata without dropping
   unknown observations or introducing future-data labels. Retain
   multiplicity correction and cost-scenario sensitivity.
3. **Portfolio stress and turnover diagnostics.** Use high correlation
   as a review input to existing group-concentration, turnover and
   derivative risk contracts; do not automatically rescale live orders
   without separate validated risk policy.
4. **Operator visibility.** Future authenticated read-only widgets may
   show cash mismatches, missing receipts, UNKNOWN market states,
   timestamps and source provenance. Never display an expected payment
   as a reconciled live cash balance.
5. **Skill and model catalog control.** Jev-tree, skillbox,
   JevRouter, pi-jev-router and the TypeSafe workflow donors offer
   partitioning, versioning, bounded selection and admission concepts,
   but no extra live model proxy or browser/shell permission owner
   should be imported on the broker execution path.
6. **Capital authority remains isolated.** Competing exchange runtimes
   from Jeeva, Jev-Trades, jev-trade, Claude Trader and Vibe-Trading
   must not supersede TradingGYM's existing single account owner,
   pre-trade gates, reservation fencing and emergency gross-flat
   evidence without independent end-to-end parity tests.

## Verification gates and limitations

The full Python 3.11/3.12 matrix, all AST/source-accountability hashes,
app-derived OpenAPI, React/Rust builds, external source attestation,
live funding receipts, model calibration and broker reconciliation must
still run on the exact PR head. The donor metadata registry is exhaustive,
but method-by-method semantic qualification of all 4,226 members is
NOT claimed. No live execution is enabled; PR #2 remains draft.
