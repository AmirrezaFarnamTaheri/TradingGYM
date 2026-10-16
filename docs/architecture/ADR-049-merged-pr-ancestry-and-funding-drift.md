# ADR-049 — Consolidation ancestry and cumulative funding parity

**Date:** 2026-10-15  
**Target:** TradingGYM PR #2, `audit/2026-10-11-execution-ci-hardening`  
**Status:** implemented in draft PR; funded-live remains **NO-GO**.

## Verified PR history — no duplicate merge needed

The GitHub comparison of PR #5's head
(`badbe51d6c8a6ccd56de3935a5f0a76f128f3e29`) to the consolidated
PR #2 head of 2026-10-15
(`341ec2326fb72ecb2cae0e1770bac65cf0428705`)
reports `ahead_by=88`, `behind_by=0` and identifies PR #5's head
as the merge base. A reverse comparison reports zero missing commits.
PR #5 was closed **without merging it into main**, but its source
commit is already in PR #2's ancestry. The formal status of PR #5 as
`closed/merged=false` is therefore consistent with a branch
consolidation followed by superseding closure. No new merge commit was
necessary.

The original targeted RL-history PR #3 and emergency-flatten PR #4 were
also closed without GitHub PR merges. Their guard conditions and named
regression tests exist in PR #2's current files; the original individual
PR head SHAs are not direct ancestors and should not be misrepresented
as independently merged.

## Funding mismatch obscured by per-event tolerances

`reconcile_funding()` previously checked
`abs(receipt.cash_delta_quote - expected_event_cash) <= tol` for each
event, but returned `consistent=True` whenever no *individual*
event violated that limit—even if many tolerated differences accrued
to a material unexplained aggregate balance per currency.

Example: two positive-rate long funding dues of -0.10 USDT each
and settled receipts of -0.11 USDT each. With `tol=0.01`, each event
independently passes. However, the aggregate shortfall is -0.02 USDT.
A reconciler should not report an all-clear at its configured absolute
currency tolerance.

The non-mutating readback API now rejects any per-currency
`abs(observed_cash - expected_cash) > tol`, emitting a deterministic
`funding_aggregate_mismatch:<CURRENCY>` blocker. It never nets
different currencies together, never posts ledger entries and never
authorizes funded-live trading.

Regression coverage includes cumulative same-currency mismatches,
the inclusive single-event boundary, tolerance-preserving aggregate
cases and separate currencies with offsetting drifts. The exact
funding module was mirrored locally for a RED-to-GREEN test:
initial **1 failing / 2 passing**, then **3/3 passing** after
the patch. An additional cross-currency regression passed locally
(**4/4** focused). The new tests are committed to PR #2.

These tests are isolated, controlled, real-function tests against a
local copy of the inspected source. They do *not* establish the complete
GitHub branch's pinned matrix, frontend/Rust, broker settlement or
venue correctness.

## Reconstructed 39-donor archive evidence

The conversation's 39 ZIP attachments were re-read without extracting
files or running any foreign scripts. SHA-256 digests were calculated
while reading ZIP members to EOF, verifying each member's CRC.

- **39** archives
- **12,473** non-directory file entries
- **11,802** distinct content SHA-256 hashes
- **2,230** archive-qualified *inferred* nested folders, including
  directory names inferred from file paths
- **3** exact path/content-identical archive pairs:
  Jev Auto Router (1)/(3), JevLoop, and Jev Ultrafast
- **5** symbolic-link entries across donor ZIPs (not extracted)
- **0** archive reading or decompression errors observed

This folder count may differ from previous inventories because
directory entries and inferred directory prefixes are counted under
different conventions. The central member paths and content hashes,
rather than a particular folder-count convention, are the stable
coverage evidence.

The combined inventory is a conversation artifact rather than a new
runtime dependency. It does not prove that all donor methods are
behaviorally equivalent, free of security issues, approved for
reuse, or suitable for live financial execution.

## Exact-head CI is blocked *before test execution*

The GitHub Actions run `38774424082` on
`341ec2326fb72ecb2cae0e1770bac65cf0428705` shows
**nine failed jobs with runner_id=0 and zero executed steps**.
Later runs triggered by funding-regression commits exhibit the same
pre-step failure. A zero-step failure is a runner allocation or
platform-side obstruction, not a Python/frontend/Rust test verdict.
This audit does not diagnose billing, quota, disabled Actions,
or organization policy without that independent platform evidence.

Required next gates:
1. Restore Actions runner availability and run the exact-head
   Python 3.11/3.12 Ubuntu/Windows, lint, type, security, frontend
   and Rust suites.
2. Recompute all source accountability hashes and regenerate OpenAPI
   from the pinned app; no manual snapshot replaces generator checks.
3. Run broker readback, funding/order settlement, emergency flatten,
   idempotency and replay/fault-injection suites.
4. Keep PR #2 draft and funded-live **NO-GO** until the actual
   qualification evidence is independently accepted.

No second PR merge, main-branch update, live trading permission
change, or broker mutation occurred during this pass.
