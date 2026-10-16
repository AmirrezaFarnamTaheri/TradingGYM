# ADR-044 — Read-only peer execution parity and second-order donor graph

**Date:** 2026-10-15  
**Target:** TradingGYM PR #2, `audit/2026-10-11-execution-ci-hardening`  
**Status:** development integration only; production/funded trading remains **NO-GO**.

## New second-order static donor analysis

The 17 new ZIPs have now undergone two complementary non-executable scans:

1. The initial full archive inventory covered all **4,226 entries**, 617
   observed nested directory paths, 4,180 unique file-content SHA-256 values
   and extracted 34,209 source symbol declarations across 3,113
   source-classified entries.
2. A second static import, call-name and declaration-span index examined
   **3,114 readable source files** across all 17 archives. It captures
   cross-file import strings, in-file symbols, calls and large routines
   without running any source. The file counts differ slightly because
   these scans use different source-extension and vendor filters.

The second pass identifies Vibe-Trading's `backtest/engines/base.py`
(2,411 lines), `options_portfolio.py` (905 lines), `_market_hooks.py`
(652 lines), exchange/account reconciliation, loader adapters and
turnover-aware optimizers as major comparison candidates.
`claude-trader` contributes independent sizing and signal-scoring tests;
`jeeva` contributes wallet/session reconciliation and funding ledgers;
`jev-trade` provides maker/IOC trading traces; and AI-Trader has experiment
ranking/metrics and collaboration structures. None of these whole engines
is imported simply because it has a similar function or model name.

The reproducible static graph and per-donor maps live in the companion
conversation artifacts `new_donor_call_graph.json` and
`new_donor_call_graph_overview.md`. File-level enumeration is comprehensive;
dynamic calls, source execution, live network behavior and semantic
equivalence have **not** been proven for every donor method.

## Implemented pure execution comparison

New `trading_gym/convergence/peer_execution_parity.py` is a pure,
read-only evidence comparator, derived from the useful reconciliation,
idempotent-fill and maker/IOC ideas in jeeva, Jev Trades, jev-trade and
Vibe-Trading. It deliberately does not create a second order executor,
accounting repository, auth bypass or model-authority path.

`reconcile_peer_execution(order, fills, broker, as_of_unix=...)` accepts
three immutable dataclass contracts: `OrderIntent`,
`FillEvidence`, and `VenueReadback`. It:

- Requires canonical order/client/asset identifiers, bounded finite
  `Decimal` values, valid lot/price increments, and integer causal
  submission/fill/readback timestamps.
- Rejects any claim of a complete readback that is missing, future-dated,
  out of scope, or lacking a 64-hex provenance reference. Provenance hash
  **shape** is checked; upstream authenticity remains a separate task.
- Requires contiguous, strictly ordered fill sequences from 1, and
  idempotently collapses only *identical* replayed execution IDs. An ID
  collision with different economics fails explicitly.
- Rejects post-only orders reporting taker fills, unknown liquidity
  modes, impossible cumulative fills and negative long-only positions.
  IOC orders cannot be left open at final observation.
- Computes signed position and base-currency cash changes from prices,
  quantities and maker rebates/taker fees with exact `Decimal` arithmetic.
  Divergent readback quantity/cash/position yields `status=drift`, not
  `matched`.
- Reports independently whether the order is finalized; even an
  internally consistent *open* order cannot be mistaken for settled.
  Always sets `execution_authority_granted=false` and includes a stable
  SHA-256 reference to canonical accepted evidence.

This comparator assumes one **isolated**, single-currency spot order, a
known opening position and cash balance, and a complete sequential fill
history. It does not model funding payments, perps, cross-margin, FX
conversions, collateral transfers, other simultaneous orders, exchange
identity signatures, gross-flat account proof or live venue execution.
Those require existing accounting/production owners and a separate
broker-qualified acceptance test.

### Chain identity normalization

A related production audit found
`trading_gym/production/chain.normalize_chain` performed arbitrary
`int(chain_id)` conversion. That accepted `True` as Ethereum 1 and
silently truncated `1.2` or `56.9` to a valid chain, while returning
inconsistent errors for `None`. The parser now accepts only actual
integer IDs or canonical decimal string IDs; booleans, floats, whitespace,
leading-zero aliases, unsupported IDs and missing values reject.
Existing canonical URL IDs remain supported.

## Verification evidence and limits

A local Python 3.13.5 **isolated module mirror** reproduced failures
before implementation:

- 7 unsafe chain-id inputs passed (or raised an inconsistent exception);
  17 chain identity cases passed after the strict parser.
- 15 execution-parity behavioral tests initially failed at the
  unimplemented public function. After implementing the comparator
  and correcting a Decimal display-format mismatch, 15 passed.
- Combined local suite: **32/32 passed**, and syntax compilation succeeded
  for the new modules. No real venue request was sent.

The implementation files and focused tests were pushed to PR #2, and the
source-accountability registry was updated for both modules. This is
**not** whole-repository/CI certification.

## Remaining acceptance gates

1. Run the exact-PR-head Python, lint, formatter, API/contract,
   frontend and Rust suites on actual allocated CI runners.
2. Compare the new read-only parity outputs with independent sealed broker
   fixtures for maker, IOC, mixed fees, rejected/late events and concurrent
   account updates; only implement additional venue-specific rules after
   their settlement conventions are verified.
3. Complete Vibe-Trading engine by engine/session by session market parity
   fixtures and AI-Trader experiments/benchmark evidence review, without
   adopting external agent command authority.
4. Vet model/skill-router modules for control-plane integration only where
   there is a real catalog-capacity or quality gap.
5. Independently verify source provenance, position/funding reconciliation
   and gross-flat emergency readback before any funded-live authorization.

The PR is draft and the safety posture is unchanged: **NO-GO**.
