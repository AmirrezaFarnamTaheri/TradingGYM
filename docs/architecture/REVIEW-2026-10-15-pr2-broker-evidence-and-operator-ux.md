# PR #2 — Broker evidence, mutation refusal and operator interface review

Date: 2026-10-15  
Status: **DRAFT / FUNDED-LIVE NO-GO**

This document is a revision-bound code review of targeted paths in the existing
PR #2 branch. It is **not** an independent certification of the complete
application or a substitute for executed Python, frontend, security, Rust,
broker, and end-to-end suites.

## Reproduced source-level failure paths and mitigation

| Boundary | Previously possible | Hardened behavior | Regression |
| --- | --- | --- | --- |
| Global panic, order list | Verifying only the configured pair could overlook open orders on other markets before retiring the global panic marker | Query all open orders for final verification; a nonzero/invalid total denies proof | `test_global_panic_proof_requests_all_open_orders` |
| Global panic, preflight | Before market reductions, pair-filtered cancellation scans could overlook unrelated open orders that might execute during the flatten | Query all open orders, verify complete pagination, refuse malformed or non-owned order references **before any mutation** | `test_global_panic_preflight_blocks_unowned_orders_before_mutation` |
| Global panic, gross positions | A flat configured base asset could coexist with other positive holdings or unrelated shorts | Check every broker currency component and every short; refuse global flat proof for nonzero unqualified exposure | `test_panic_global_flat_proof_rejects_other_asset_holdings`, `test_panic_global_flat_proof_rejects_unrelated_short` |
| Live decision loop | A reported failed/denied reconciliation could be counted as an ordinary retryable loop error and followed by later bar decisions | Stop immediately with `UnrecognizedExecutionStatus`; require manual brokerage reconciliation before another funded decision | `test_failed_live_reconciliation_halts_without_retrying_next_bar`, updated short-denial test |
| Account equity | Invalid balance components could net to zero or be ignored before validation; unrelated live short P&L was not included | Validate every available/blocked component, reject duplicate currencies and malformed shorts, deny equity admission when another instrument has an unpriced active short | `test_live_equity_rejects_invalid_balance_components`, `test_live_equity_rejects_invalid_short_inventory` |
| Mutation policy | Floating-point attempt counts, unbounded/nonfinite max delay, and jitter above the alleged maximum were admissible | Require integer attempts, finite bounded delays, valid exception classes; hard-cap jitter outcome | `test_mutation_attempt_budget_must_be_bounded_integer`, `test_mutation_retry_max_delay_must_be_finite_and_positive`, `test_retry_delay_never_exceeds_absolute_maximum` |
| Historical market coverage | Multiple off-cadence intrabar timestamps could count as full bar coverage despite omitting completed candles | Require timestamp spacing to be an integer multiple of the requested timeframe; preserve actual missing-bar gaps | `test_market_coverage_cannot_be_fabricated_from_subinterval_ticks`, `test_market_coverage_accepts_valid_missing_bar_gaps_without_filling` |
| Production UI | Placeholder text carried no authoritative account-halt evidence | Read-only authority, emergency, and managed-process snapshots; conflict/unavailable states are unverified, not healthy; explicit refresh | `ProductionPanel.test.jsx` |
| Authorization UI | A prior ALLOWED result remained visible for a newly typed scope/principal, and one click could terminate a grant | Bind verdicts to the exact checked scope/principal; hide stale results, invalidate after revocation; require explicit second confirmation | `AuthzPanel.test.jsx` |

## Independence and scope of evidence

- The emergency signal is shared, so final acknowledgment must not rely on
  a symbol-filtered order query or on net rather than gross positions.
- Account-wide verification is **conservative evidence admission**, not a
  full implementation of multi-symbol emergency liquidation. Positive
  unrelated exposure leaves the halt pending.
- The broker snapshots are still independent reads, **not a broker-provided
  atomic account transaction**. Other hosts may place orders between reads.
- These changes do not add any funded order route or relax approval policies.
- A one-hour request budget is an operator count ceiling, not a token, money,
  or provider quota oracle. Multi-key rotation cannot establish billing safety.

## Release blockers still requiring independent work

1. **Authoritative broker reconciliation**: order identity, fills, fees, collateral,
   all open-order pages, and unknown submission outcomes across restarts.
2. **Global emergency**: fault-tested multi-symbol gross-neutral strategy,
   atomic account-wide evidence or venue-side trading freeze, and recovery
   from partially acknowledged hedges/cancellations.
3. **Distributed capital control**: one transactional account-owning ledger,
   cross-host worker fencing, idempotent reservations, and durable replay.
4. **Production security**: independent identity/role qualification,
   provenance and signed release artifacts, disaster recovery and load tests.
5. **Exact-head execution**: Python 3.11/3.12 Ubuntu/Windows, frontend Vitest
   and build, lints/types, Rust parity, API/websocket, security scan, and
   broker fault-injection suites with retained receipts.

## Validation commands and evidence integrity

The repository's CI workflow defines the authoritative gates. At the latest
inspected head on October 15, sampled failed GitHub Actions jobs still
reported zero executed steps; they provide neither passing tests nor a source
failure traceback. The connected repository can be read/written through the
GitHub API, but the isolated container cannot resolve github.com to clone
the exact private checkout.

New regression tests are committed, not represented as executed full-suite
results. A standalone Python reference oracle for the bounded retry logic
ran 16/16 cases on Python 3.13, which only verifies that isolated logic and
**does not** certify the imported repository module.

**Release decision:** Keep PR #2 draft. No merge, deployment, funded execution,
or relaxation of broker safeguards on this evidence.
