# ADR-054 — Strategy source integrity and settlement cash canonicalization

**Date:** 2026-10-16  
**Status:** Integrated on draft PR #2; exact-head CI and end-to-end release qualification remain **unverified**.  
**Safety boundary:** Research and settlement projection only; no model, broker, order, approval or funded-live authority is granted.

## Source-level findings and resolutions

The expanded 17-archive convergence (4,226 archive entries) provides a broad architectural inventory, but source-name overlaps are not proof of behavioral equivalence. A separate non-executing Python AST pass examined **2,347 Python files** and **21,793 nontrivial function/class syntax nodes** (each at least 750 normalized AST characters). It found **21,572 distinct fingerprints** and **no exact normalized subtree match across distinct archives** using a comparison that disregards function/class names and initial docstrings. That does **not** establish that the donor implementations are semantically unrelated; it specifically cautions against pretending similarly named methods are interchangeable.

### 1. Indicator aliases cannot shadow financial evidence

`StrategySpec.validate_references` now rejects aliases that collide with raw OHLCV names or the computed `entry_signal`, `exit_signal` and `position_intent` columns. The lower-level `compute_indicators` API independently blocks existing-field and duplicate-alias collisions even when called outside the declarative schema. Both the evaluator and direct indicator API now reject frames with duplicate column labels.

**Why:** Overwriting an input `close` with an SMA (or reusing an alias) can silently change what a later `field("close")` predicate means, producing an invalid research signal. This is an evidence-integrity failure, not a harmless naming preference.

### 2. Simultaneous entry and exit favor exit

`evaluate_strategy` now computes its vectorized `position_intent` with **exit precedence** when entry and exit predicates are both true on the same bar. A risk-reduction/exit signal cannot be masked by a fresh entry in the same row. This is a research-intent ordering rule, not permission to short an account: actual position/order meaning remains owned by execution policies and brokers.

### 3. Canonical currency aliases must not overwrite balances

`evaluate_settlement` previously uppercased settled-cash keys into a dictionary without duplicate detection. Supplying both `"USD"` and `"usd"` silently overwrote one account balance. It now requires finite bounded `Decimal` values and ASCII currency symbols matching canonical `[A-Z0-9]{2,20}` after uppercase normalization, rejects colliding canonical keys, and rejects non-ASCII input that uppercases to ASCII (e.g. `ß` -> `SS`).

Single lowercase keys remain accepted and normalize deterministically; invalid symbols, malformed types and NaN/infinite balances generate a stable domain error. This is a calculation/input assurance change, not a transfer, bank posting, or custody reconciliation.

### 4. Currency-only cash cannot net unrelated accounts

The settlement report's `settled_cash` input is keyed by currency, not by
account, while each `SettlementObligation` carries an `account_id`.
A report with obligations from two accounts can otherwise assume that one
account's positive cash satisfies the other's liabilities. The evaluator
now **refuses multiple distinct account IDs** until a separate per-account
cash input/ledger is specified.

An existing negative settled balance is now reported as a
`settlement_cash_shortfall:<CURRENCY>` blocker even if there are no
pending payables. Prior logic flagged a shortfall only when iterating
pending negative obligations, masking a pre-existing cash deficit.

The single-account analytical path, including independent pending-payable
deductions, remains unchanged. These are safeguards on a read-only report,
not evidence of settlement authority or a real custody ledger.

## Added regression contracts

- `tests/strategy_lab/test_alias_and_exit_integrity.py`: raw/derived alias collisions; direct lower-level API; duplicate market columns; exit-over-entry precedence.
- `tests/accounting/test_settlement_cash_canonicalization.py`: canonical currency collisions; non-ASCII alias spoofing; invalid input types and finite amounts; lossless single-key normalization.
- `tests/convergence/test_settlement_plane.py`: multi-account cash netting refusal and existing negative-balance blocker.

**Verification limitation:** These tests are committed with the implementation, but the available environment cannot clone the complete authenticated PR checkout, and GitHub Actions jobs have continued to fail **before executing any steps** (the earlier inspected run `38878980999` reported zero steps across all failed jobs and no downloadable job log). Do not describe them as passing exact-head CI until a runner successfully executes the pinned tests.

## Second-order convergence: retained single owners

| Concern | Owner in TradingGYM | Relevant donor principles | Integration decision |
| --- | --- | --- | --- |
| Causal strategy indicators, source names, entry/exit intent | `strategy_lab/models.py`, `indicators.py`, `evaluator.py` | Claude Trader + Vibe indicator libraries | Adopt independently tested indicators and robust source/alias invariants, not a second signal executor |
| Held-out strategy comparison, costs, multiplicity | `experiments/peer_strategy_comparison.py` and `strategy_lab/overfitting.py` | AI-Trader experimental scorecards + Vibe evidence harness | Retain source-sealed, non-promoting research reports |
| Allocation planning, turnover and group constraints | `institutional_os/portfolio_construction.py`, `portfolio/rebalance.py` | Vibe turnover-aware optimization | Preserve the existing portfolio owners; no silent broker routing |
| Currency/funding/venue settlement | `accounting/settlement.py`, `accounting/funding_reconciliation.py` | jeeva funding audit + jev-trade sleeve evidence | Require canonical money units, pending-liability accounting and independently confirmed readback |
| Model/skill selection and governance | `intelligence/model_route_preview.py`, `skills/capability_selection.py` | skillbox, JevRouter, pi-jev-router | Bounded, opt-in research hints; no dynamic unreviewed financial tool authority |
| Execution and financial emergency | existing production/venue authorities | Jev Trades, jeeva, Vibe execution traces | No donated runtime may bypass broker readback, gross-flat proof, account fencing or rollout grants |

## Release gate

Run on **one exact current PR head**:

```sh
poetry run pytest -q tests/strategy_lab/test_alias_and_exit_integrity.py tests/accounting/test_settlement_cash_canonicalization.py
poetry run pytest -m "not integration" -q
poetry run python scripts/audit_module_accountability.py --check
poetry run python scripts/export_openapi.py --check
poetry run ruff check .
poetry run black . --check
```

Then complete the frontend/Rust/security/venue and broker fault-injection matrices. A green isolated unit test is not evidence of profitable trading; a clean source graph is not a custody or execution approval.

**Disposition:** Draft PR, unmerged; funded-live **NO-GO** pending runner-backed verification and the pre-existing independent financial authority gates.
