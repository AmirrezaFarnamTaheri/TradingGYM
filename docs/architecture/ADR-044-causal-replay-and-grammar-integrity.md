> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-044 — Causal Strategy Research Replay and Strict Grammar Admission

**Date:** 2026-10-15  
**Status:** committed to draft PR #2, not production-qualified  
**Authority:** research/advisory only; funded-live **NO-GO**

## Why this convergence matters

Source comparison of the 17 new donor archives identified behavioral
requirements more valuable than absorbing a second monolithic trading runtime.

- Vibe-Trading's `agent/tests/test_execution_causality.py` protects against
  a decision-bar close influencing that same bar's open order.
- Vibe-Trading's `agent/tests/test_rebalance_execution_evidence.py` separates
  requested allocations from actual venue fills and funding measurements.
- AI-Trader's experiment scoring isolates comparison evidence from running
  trader processes.
- Jev Trades and jeeva model paper/venue order lifecycles, but their live
  brokerage actors cannot be ported without risk-grant and reconciliation
  qualification.

TradingGYM already has declarative strategies, purged walk-forward tests,
budgeted backtest planning, portfolio rebalancing and broker authority
separation. A small **read-only causal replay** adds more value than a
parallel execution system.

## Implemented public interfaces

### Next-open replay

`trading_gym.agent.strategy_lab.causal_replay` now exports:

- `replay_evaluated_signals(frame, *, source_sha256,
  allocation_fraction=0.1, fee_bps=0, slippage_bps=0,
  initial_equity_base=1) -> CausalReplayReport`.
- `replay_strategy(frame, strategy: StrategySpec, *,
  source_sha256, fee_bps=0, slippage_bps=0,
  initial_equity_base=1) -> CausalReplayReport`.

The latter passes the existing canonical `evaluate_strategy` output
through the former and bounds starting exposure by
`strategy.risk.max_position_fraction`. Neither submits an order.

Closed-bar signal at index *t* is eligible for a **hypothetical** trade
only at `open[t+1]`. A signal on the final bar remains explicitly
unfilled. A bullish closed-bar mark never receives credit for that
bar's earlier intrabar price movement. The cash/units ledger records
long-only entries, exits, per-side fee plus slippage estimates and a
mark-to-market close at each observed bar.

Entries are self-financed: fractional exposure is divided by
`1 + (fee_bps + slippage_bps)/10_000`, preventing fee spending from
manufacturing cash. A non-authoritative report contains trade counts,
estimated cost/turnover, net return, worst close-mark drawdown,
open-position status, a caller-supplied source hash and an independently
computed data/signal hash. It unconditionally reports
`execution_authority_granted=false`, `mutation_performed=false`,
`venue_fills_verified=false` and `stop_loss_simulated=false`.

### Input and evidence boundaries

The pure replay rejects invalid or conflicting entry/exit signals,
contradictory integer `position_intent`, duplicate or non-monotonic
timestamps, corrupted/non-finite OHLCV bars, unsupported booleans as
costs, oversized work (250,000 bars maximum), and malformed SHA
references. The replay hashes actual bar values, timestamps and signals
in a deterministic canonical order, alongside the report summary.

The caller-supplied `source_sha256` is **not a proven exchange
attestation**; a matching report hash is reproducibility, not an external
assertion of original market truth.

### Grammar and indicator integrity

`StrategySpec` and the standalone `compute_indicators` reject alias
collisions with OHLCV input columns, other indicators, or evaluator
outputs (`entry_signal`, `exit_signal`, `position_intent`).
A fitted indicator must never overwrite the evidence it was computed
from or masquerade as an execution decision.

Pydantic's default numeric coercion was independently reproduced:
`Operand(kind="constant", value=True)` became 1.0, and boolean
risk inputs such as `max_position_fraction=True` became 100% exposure.
Both raw boolean and non-finite strategy constants are now refused
before coercion. Numeric RiskSpec fields reject raw booleans, and
non-finite indicator periods yield a deliberate validation error
instead of an uncaught integer conversion overflow.

The branch already contained separate flat-market RSI and stochastic
smoothing corrections. This pass preserved those changes rather than
reintroducing redundant indicator algorithms.

## Verification evidence and boundaries

A local Python 3.13.5/Pandas/Pydantic mirror recorded RED cases for
the above behavior and GREEN runs after implementation.

- 14 next-open replay cases passed, then an additional contradictory
  `position_intent` case failed before repair and passed afterward:
  **15 replay cases**.
- Numeric-grammar reproduction exposed **13 failing of 18 cases**
  before validation and passed **18/18** after validation.
- RSI neutrality, monotonic trends and custom stochastic %D warmup
  passed **3/3** in the corrected local mirror.

Together the latest isolated tests report **36 passed** and syntax
compilation successful. These tests exercise mirrored algorithms; they
are **not** an exact-head checkout of the pinned TradingGYM environment.
A separate GitHub regression suite was added under
`tests/strategy_lab/test_causal_next_open_replay.py`,
`test_indicator_integrity_convergence.py` and
`test_grammar_numeric_integrity.py`, but cannot yet claim hosted CI
success.

## Second-order architecture and remaining gaps

1. **Historical truth and latency:** bind candles to exchange event-time,
   source snapshots, revisions, corporate actions and venue settlement
   calendars; add a fill-latency distribution. The current replay only
   assumes the subsequent bar open would have been tradable.
2. **Broker parity:** simulate maker/post-only versus taker/IOC, partial
   fills, min size, price increments, order latency, stops, funding,
   margin/liquidation and cancellations only against independently
   testable venue fixtures; never claim these are modeled now.
3. **Portfolio and multi-currency:** compare Vibe's multi-engine currency,
   rebalance causality, turnover penalties and instrument-specific fees
   against TradingGYM's existing `portfolio/rebalance.py`,
   `market/calendar.py`, `risk/derivatives.py` and venue routing.
   Do not sum USD, USDT, CNY and KRW cash without verified FX marks.
4. **Experiment promotion:** combine independently prepared, purged
   holdouts with AI-Trader's experiment comparison concepts, causal
   replay cost assumptions and multiplicity-adjusted selection. Report
   research quality separately from any model or capital authorization.
5. **Operator UI:** a read-only replay comparison tab can expose
   assumptions, accepted versus ignored signals, metrics and SHA-bound
   evidence. It must not display hypothetical transactions as executed
   broker orders.
6. **Release gates:** exact-head Python 3.11/3.12 unit and integration
   suite, black/ruff/mypy, frontend tests/build, Rust checks, OpenAPI
   generation, every module-accountability SHA and broker reconciliation
   must all pass before releasing.

**Disposition:** research capability committed, but exhaustive semantic
convergence of all 4,226 donor files remains an open evidence program.
The draft PR stays unmerged and funded-live remains **NO-GO**.
