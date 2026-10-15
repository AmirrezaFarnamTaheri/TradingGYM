# Expanded Peer Trading and Agent Convergence Implementation Plan

> **For agentic workers:** Use the host's available task-by-task implementation workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Absorb demonstrably valuable capabilities from the 17 new donor
archives into TradingGYM PR #2 while preserving existing source, model and
funded-trading authority boundaries.

**Architecture:** Keep TradingGYM's current declarative strategy grammar,
research evaluation, venue reconciliation, governed model fleet, and operator
workspace as single owners. Import *ideas and independently tested behaviors*
at those interfaces rather than introducing donor runtimes, keys or duplicate
brokers. Treat all AI advice, research strategies and optimizer outputs as
non-authoritative until independent execution gates certify them.

**Tech Stack:** Python 3.11/3.12, NumPy/Pandas/Pydantic/FastAPI/Pytest;
React/Vitest; existing Rust crates; GitHub Actions.

## Global Constraints

- Existing PR #2 is the implementation branch; preserve its changes.
- No donor installation scripts, arbitrary agents, binary releases or live
  broker credentials may execute as part of donor inspection.
- Source-level inventory of all 4,226 new archive entries and folder hierarchy
  is complete; method-level equivalence of every donor is not proved by the
  AST/lexical source index.
- Treat licenses as a user-managed review assumption for *candidate selection*,
  not an assertion that all code legally has MIT permission.
- New strategy presets and model advice are research-only and may not grant
  funded-live trading privileges, model promotion or capital authority.
- Require exact-head CI, restored runner allocation and venue readback before
  describing the PR as releasable.

---

### Task 1: New-donor evidence register and overlap isolation

**Files:**
- External artifact: `new_archives_full_inventory.json`
- External artifact: `new_archives_nested_folders.json`
- External artifact: `new_archives_source_symbols.json`
- External artifact: `new_archives_convergence_register.json`
- Created in repo: `docs/architecture/ADR-043-expanded-strategy-and-donor-convergence.md`

**Interfaces:** ZIP central directory and content SHA-256 -> metadata,
folder counts, exact duplicates, candidate source paths and quality labels.

- [x] Enumerate and hash every archive file without executing it.
- [x] Enumerate every nested folder, and extract Python AST plus bounded
  lexical symbols for other supported languages.
- [x] Verify three uploads are file-content-identical to earlier snapshots,
  avoiding duplicate feature ports.
- [x] Publish per-donor high/medium/method-level candidate mapping, with
  unreviewed components labeled as such.
- [ ] Have independent reviewers compare behavior, test fixtures and
  source-license obligations for the highest-value Vibe-Trading,
  jeeva and AI-Trader submodules.

### Task 2: Causal ADX / directional trend research family

**Files:**
- Create: `trading_gym/strategy_lab/adx.py`
- Modify: `trading_gym/strategy_lab/indicators.py`
- Modify: `trading_gym/strategy_lab/models.py`
- Modify: `trading_gym/strategy_lab/templates.py`
- Modify: `trading_gym/strategy_lab/validation.py`
- Test: `tests/strategy_lab/test_adx_donor_convergence.py`

**Interfaces:**
- Consumes: `compute_indicators(frame, list[IndicatorSpec])`,
  `StrategySpec`, `evaluate_strategy(frame, strategy)`.
- Produces: `compute_adx(frame, period=14, component="adx")` with
  `adx`, `plus_di`, `minus_di`, and `dx`; two bounded research presets.

- [x] Write prefix-causality, flat-market, directional and invalid-input
  tests; observe missing-behavior failure.
- [x] Implement Wilder smoothing and reject non-finite/crossed inputs.
- [x] Detect incorrect initial EWM seed via hand-calculated 2-period RED
  regression; replace with exact Wilder sum seed.
- [x] Add grammar component validation, 2-period warmup and two
  risk-bounded declarative templates.
- [x] Demonstrate template entry/exit predicates on synthetic regime
  reversals; no claims of profitability.
- [ ] On a full checkout: run
  `poetry run pytest -q tests/strategy_lab/test_adx_donor_convergence.py tests/convergence/test_strategy_lab.py tests/strategy_lab/test_strategy_variants.py`.
  Expected: all pass, no unexpected warnings.

### Task 3: Causal market-index validation

**Files:**
- Modify: `trading_gym/strategy_lab/evaluator.py`
- Test: `tests/strategy_lab/test_bar_time_integrity.py`

**Interfaces:** `validate_market_frame(frame: pd.DataFrame) -> None`.

- [x] Reproduce acceptance of shuffled, duplicate and missing timestamped
  candles at the research evaluator boundary.
- [x] Reject corrupted `DatetimeIndex` ordering before rolling feature
  calculation; preserve monotonically ordered timestamps and RangeIndex
  fixtures.
- [x] Verify 4/4 standalone index tests, including accepted legacy input.
- [ ] Independently test event-time conventions on mixed/naive/timezone
  feeds and the broker's closed-bar execution delay before production use.

### Task 4: Cross-venue portfolio and execution parity

**Files:**
- Existing: `trading_gym/portfolio/rebalance.py`
- Existing: `trading_gym/institutional_os/portfolio_construction.py`
- Existing: `trading_gym/risk/derivatives.py`
- Proposed test: `tests/convergence/test_peer_execution_parity.py`

**Interfaces:** existing
`plan_rebalance(...)`, `construct_portfolio(...)`,
`analyze_derivative_risk(...)`; external donor inputs are fixture files
with pinned source hashes and explicit decimal currency/fee/tick units.

- [ ] Add independent fixture tests for rounding, maker/taker fees,
  post-only rejection, IOC partial fills, cancellation/replace idempotence,
  negative funding, liquidation buffers and venue gross-flat readback.
  Expected: a missing or contradictory settlement receipt rejects live
  eligibility without altering existing holdings.
- [ ] Compare jev-trade sleeve isolation, jeeva funding ledger and Vibe
  portfolio turnover penalties against current guarded semantics; port
  only fixture-proven differences into the *existing* responsible module.
- [ ] Verify no test submits a live order, uses donor credentials or
  creates a second authoritative broker path.

### Task 5: Evidence-first multi-strategy research comparisons

**Files:**
- Existing: `trading_gym/strategy_lab/overfitting.py`
- Existing: `trading_gym/experiments/sealed_spec.py`
- Proposed: `trading_gym/experiments/peer_strategy_comparison.py`
- Proposed test: `tests/experiments/test_peer_strategy_comparison.py`

**Interfaces:** bounded, sealed research strategies and as-of returns;
produce reproducible ranking evidence with method assumptions, not trading
grants.

- [ ] Add failing tests for hindsight-contaminated labels, overlapping
  train/test regimes, multiplicity-adjusted selection, missing fill fees,
  zero-trade strategies and identical candidate digests.
- [ ] Implement deterministic per-candidate replay receipts, net-of-cost
  metrics, uncertainty and non-promotable model-comparison status.
- [ ] Compare against AI-Trader's experiment/challenge scoring and Vibe
  factor/cost evaluation; reject unbounded social leaderboard designs.

### Task 6: Versioned capability/skill taxonomy and research UX

**Files:**
- Existing: `trading_gym/intelligence/model_route_preview.py`
- Existing: `trading_gym/agent/forge.py`
- Existing: `frontend/src/components/IntelligencePanel.jsx`
- Proposed tests: `tests/agent/test_capability_selection.py` and
  `frontend/src/components/__tests__/StrategyResearchPanel.test.jsx`

**Interfaces:** immutable model/tool/skill manifest references and a
non-authoritative, read-only research selection report.

- [ ] Compare skillbox/type-safe routers against current grants; tests
  reject unknown executable skills, missing consent, untrusted manifests,
  stale model prices, exhausted quotas and deceptive owner labels.
- [ ] Only add Jev-tree hierarchical choice partitioning if a verified
  catalog exceeds the supported 255-option native limit; otherwise keep
  the current bounded selector.
- [ ] Expose only role-scoped research evidence and approved template
  metadata in the operator UI; never present simulated fills as live.

### Task 7: Exact-head integration, provenance and safe release decision

**Files:**
- Existing: `docs/contracts/module_accountability.json`
- Existing: `docs/contracts/openapi.json`
- Existing: `.github/workflows/ci.yml`
- Existing: `RELEASE_STATUS.md`

**Interfaces:** one pinned Git SHA -> full CI and venue validation receipts
with explicit pass/fail/unsupported states.

- [x] Refresh source-accountability manifest for the new indicator and
  strategy-source changes, and update the published IndicatorSpec enum.
- [ ] On an actual checkout of the final head run
  `poetry run python scripts/audit_module_accountability.py --check`
  and `poetry run python scripts/export_openapi.py --check`.
- [ ] Run `poetry run pytest -q`, `poetry run black . --check`,
  `poetry run ruff check .`, frontend test/build, Rust fmt/clippy/test,
  offline network/broker fault-injection and independent PR review.
- [ ] Publish a source-SHA-bound release receipt listing every result,
  and retain draft/NO-GO status until all critical gates actually pass.

## Unresolved Product Decisions

- Which new countries/exchanges merit full data-loader support, and which
  observable venue specification and legal access determine readiness.
- Whether multi-agent research tournaments should be publicly visible or
  restricted to private, sealed internal experiments.
- Funding-model and maker/IOC parity tolerance by actual broker and venue.
- Whether hierarchical model/skill catalog selection has enough measured
  scale benefit to justify another runtime dependency.
- What measured model quality, calibration, and real settlement evidence
  would qualify *paper-only* promotion; funded-live qualification remains
  a separate independent risk and authorization process.
