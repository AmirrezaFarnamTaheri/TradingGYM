# ADR-052 — Lossless accounting, safe skill-archive intake, and contradictory bar admission

**Date:** 2026-10-16. **Status:** implemented on draft TradingGYM PR #2;
isolated regressions passing; exact-head CI and funded-live certification not
verified. This is a continuation of the 39 donor-archive convergence work
(22 earlier archives plus 17 subsequent, including repeated snapshots).

## Existing owners retained

The repository already contained
`trading_gym/accounting/order_fill_parity.py`,
`trading_gym/convergence/peer_execution_parity.py`,
`trading_gym/experiments/peer_strategy_comparison.py`,
`trading_gym/skills/intake.py`, and
`trading_gym/market/data_router.py`.
The donor feature comparison **did not** justify adding another broker,
reconciliation engine, live trading agent, or independent portfolio selector.
All edits are to existing owner seams or one new, isolated static-intake
helper plus CLI. Vibe-Trading's fill-derived execution evidence, jeeva's
readback/funding ledger, and Skillbox's versioned skill package safety were
reviewed as comparison points, not imported as uncontrolled runtimes.

## 1. Decimal arithmetic: exact financial receipt vs rounded display

Two independent defects were reproduced:

- The main `order_fill_parity` aggregate used Python Decimal's ambient
  28-significant-digit context for individual fill multiplication, sums and
  account deltas, so a valid `123456789.123456789 * 987654321.987654321`
  fill could falsely disagree with an independently exact broker balance.
  Even unary negation of the high-precision sum caused rounding when done
  outside its precision context.
- The peer readback parity routine aggregated under a high-precision context
  but called `.normalize()` **outside** that context to produce its string
  report, discarding fractional evidence. Its prior test compared two
  values rounded by the same mistaken method.

The existing main reconciler now uses local precision-96 helpers for
notional, fee, quantity, cash and position arithmetic, including fee-ceiling
comparison; Decimal `copy_negate()` preserves signs without context
rounding. Incoming ledger Decimals now reject pathological coefficient
lengths and scales before modulo/tick arithmetic, and exact
`copy_abs()` prevents context-rounded bounds checks. The peer receipt
formatter now normalizes under local precision 96. Report arithmetic remains
read-only, and no matched report grants execution or settlement authority.

**RED evidence:** default-context cash balance differed from the exact
value by hidden fractional units; default-context `normalize()` also
changed its string representation. **GREEN:** local, independently checked
Decimal-mirror tests for exact aggregation, multi-order accumulation,
input scale and receipt serialization.

## 2. Complete but inert skill-ZIP inspection

`trading_gym/skills/archive_intake.py` is a new self-contained read-only
inspect-only helper; `scripts/inspect_skill_archive.py` provides a
sanitized CLI:

```bash
python scripts/inspect_skill_archive.py --archive path/to/skill.zip
python scripts/inspect_skill_archive.py --archive path/to/skill.zip --fail-on-risk
```

It uses a no-follow regular-file descriptor and strict archive, member,
uncompressed-total and decompression-ratio limits. It enumerates all nested
folders and files within its input budget; rejects absolute/traversal/
backslash/drive paths, casefold/Unicode-normalized aliases, symlinks,
unsupported file types, missing manifests and encrypted members. Member
contents are hashed and scanned for conservative code, network,
executable, secret and nested-archive markers **without extraction**.

It returns SHA-256-backed file metadata and a quarantine report,
**never** runs embedded code, installs a skill, sends credentials, grants
authority, or treats static scan success as a trusted publisher signature.
Nested archives are **flagged, not recursively unpacked**; if present they
require separate human review. The `--fail-on-risk` CLI exits nonzero
for risk-marked archives, while ordinary inspection provides useful
evidence without printing secret-bearing file contents.

The locally tested helper scanned the supplied
`skillbox-main(2).zip`: **105 files / 18 nested folder paths**, reporting
conservative risk markers with `quarantined=true`,
`activated=false`, `executed=false`. It performed no extracted-file
writes or archive code execution.

**TDD:** 15 archive API cases and 3 CLI cases passed in the local isolated
Python harness; one RED case was recorded before implementation. The exact
committed module SHA-256 matched the locally tested source.

## 3. Contradictory exchange bars cannot be silently selected

The existing `DataProviderRouter._normalise` previously selected the
last duplicate timestamp even when the two OHLCV rows differed, and
silently filtered out a NaT timestamp. For causal market evidence,
conflicting candle data is a source-integrity failure rather than an
idempotent replay.

The router now rejects missing timestamps **before** date-window filtering
and rejects disagreements in required OHLCV columns among all rows sharing
a timestamp. Repeated **identical** candles retain the existing safe
deduplication behavior and warning, avoiding unnecessary failover.
Existing cadence, completeness, circuit-breaker and provider-routing
policies remain unchanged. The new tests cover both denial cases plus
the benign duplicate case. Market provenance hashes still require
separate source authentication.

## Verification and branch hygiene

- Local focused RED-to-GREEN seam harness: **25 passing tests** across
  accounting, peer reporting, market-data admission, skill archive API
  and skill CLI; Python modules compiled without syntax errors. These tests
  use source mirrors for the existing accounting/market modules and are not
  the full repository checkout suite.
- The user-provided Skillbox ZIP was inspected with the actual new local
  helper, without extraction or activation.
- `docs/contracts/module_accountability.json` was synchronized with
  changed source SHA-256 digests. A Git-tree enumeration revealed one
  unrelated preexisting omission `trading_gym/risk/funding_history.py`;
  its digest was registered. The source-tree/manifest path count should now
  be **365 / 365**, pending final exact-head recheck.
- The GitHub-hosted CI runner previously failed **before executing job
  steps**. Thus Python, Rust, frontend and broker checks on this exact head
  must still be run and independently assessed.
- PR #2 remains draft and unmerged; actual real-venue fill/fee/funding
  reconciliation, independent gross-flat emergency readback and funded-live
  authorization remain **NO-GO**.

## Higher-order implications

1. A single shared **data-observation validity** contract should govern
   market feeds, strategy packets and held-out experiment timelines;
   manual sorting/deduplication is only appropriate when evidence is
   identical, never when market values conflict.
2. A single **financial Decimal policy** (coefficient precision, scale,
   context scope and exact display) should be applied systematically
   across all order, funding, P&L and portfolio modules with independently
   sourced decimal fixtures.
3. Model/skill/plugin selection should consume **quarantined, digested,
   capability-declared** content, never directly import an untrusted ZIP;
   independent authentication, role separation and static/runtime sandbox
   policies remain separate.
4. Release promotion must bind the exact PR-head source hashes, test
   commands, workload fixtures, operator approvals and venue results
   in an independently verified receipt. There is no evidence of funded-live
   readiness in this convergence pass.
