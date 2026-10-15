# Seventeen-Donor Strategy Convergence Implementation Plan

> **For agentic workers:** Use the host's available task-by-task implementation workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Converge the 17 uploaded donor archives onto TradingGYM PR #2 by strengthening tested, research-only strategy primitives without introducing competing execution authority.

**Architecture:** Inspect every archive entry and group substantive modules into trading strategy, evidence/portfolio, and agent-routing planes. Extend TradingGYM's bounded declarative `IndicatorSpec` rather than importing independent strategies that bypass the existing market validation, risk gates, portfolio ledger or capital authority. Use conservative defaults and publish honest import/adoption evidence.

**Tech Stack:** Python 3.11/3.12, pandas, NumPy, Pydantic v2, pytest; existing TradingGYM Strategy Lab.

## Global Constraints

- Build on existing draft PR #2; preserve current branch and existing functionality.
- Do not auto-register new live providers, execute trades, or turn model recommendations into execution authority.
- No historical lookahead: each indicator at timestamp `t` must depend only on candles closed at or before `t`.
- Treat every archive as a source for *ideas* and behavioral comparisons; metadata indexing alone does not prove code parity.
- Preserve existing Python/API/frontend contracts unless an implementation test demonstrates a necessary change.
- Do not falsely claim that every donor function, model, license, build or integration was verified.
- Keep affected module-accountability SHA-256 hashes synchronized with the final PR head.

---

### Task 1: Archive inventory and capability matrix

**Files:**
- Create: `docs/architecture/ADR-045-seventeen-donor-strategy-convergence.md`
- Inspect: the 17 attached ZIP archives and their nested modules
- Test: archive hashing, file count, duplicate resolution, symbol inventory completeness

**Interfaces:**
- Consumes: the archive-member paths, content hashes and extracted Python/TS/Rust names.
- Produces: per-donor capability matrix and target-side integration decisions.

- [x] **Step 1: Index ZIP entries and reject oversize/unreadable metadata.** Record 4,226 files, 3,223 source-classified entries, and 4,180 distinct hashes in the conversation artifact.
- [ ] **Step 2: Evaluate module clusters** against current PR #2 source contracts.
- [ ] **Step 3: Record adoption and exclusions** with source-level evidence and exact targets.

### Task 2: Causal research indicator primitives

**Files:**
- Modify: `trading_gym/strategy_lab/indicators.py`
- Modify: `trading_gym/strategy_lab/models.py`
- Modify: `trading_gym/strategy_lab/validation.py`
- Test: `tests/strategy_lab/test_volatility_flow_convergence.py`

**Interfaces:**
- Consumes: validated OHLCV candles and `IndicatorSpec(name, alias, params)`.
- Produces: `realized_volatility` (per-bar rolling log-return standard deviation or its past-only rolling percentile) and `mfi` (0–100 money-flow index).

- [ ] **Step 1: Add failing warmup/flat-volume/causality tests.** Assert no valid volatility percentile before `period+lookback-1`; MFI flat 50 and up/down extremes; future suffix invariance; invalid component/period rejected at the schema.
- [ ] **Step 2: Confirm RED** with focused `pytest -q tests/strategy_lab/test_volatility_flow_convergence.py`.
- [ ] **Step 3: Implement** only pandas rolling computations on past+current closed bars, with no external feeds and no strategy permission changes.
- [ ] **Step 4: Confirm GREEN** on identical focused test command.
- [ ] **Step 5: Run integrated Strategy Lab and module-accountability checks** before committing the reviewed PR change.

### Task 3: Two research-only declarative templates

**Files:**
- Modify: `trading_gym/strategy_lab/templates.py`
- Test: `tests/strategy_lab/test_volatility_flow_convergence.py`

**Interfaces:**
- Consumes: existing `StrategySpec`/ConditionGroup grammar and Task 2 indicator definitions.
- Produces: `volatility_regime_breakout` and `mfi_trend_confirmation` entries returned by `template_catalogue()`.

- [ ] **Step 1: Add failing evaluation tests** checking fixture causality, nonempty intentional signals, no future-window dependency and valid risk bounds.
- [ ] **Step 2: Confirm RED** before implementation.
- [ ] **Step 3: Compose templates** with guarded entries, clear risk parameters and conservative exits; mark each research-only.
- [ ] **Step 4: Confirm GREEN** and run existing template-catalogue consistency tests.
- [ ] **Step 5: Commit** with documentation of unverified alpha/performance and limits.

### Task 4: Second-order donor convergence and release evidence

**Files:**
- Update: `docs/architecture/ADR-045-seventeen-donor-strategy-convergence.md`
- Update: `docs/contracts/module_accountability.json` generated from exact module contents
- Update: `RELEASE_STATUS.md`

**Interfaces:**
- Consumes: module capability matrix, changed source hashes, focused test and CI outcomes.
- Produces: plan of missing higher-level strategy/portfolio/model orchestration surfaces without creating new capital authority.

- [ ] **Step 1: Reassess all 17 donors** for higher-level modules (hierarchical strategy search, workflow policy, multi-venue evidence, factor libraries, multi-agent review, feedback calibration).
- [ ] **Step 2: Verify output and publish** source-level limitations and test evidence.
- [ ] **Step 3: Confirm open PR head and required checks**; do not merge until full suite and live safety gates pass.

## Unresolved externally observable decisions

- Strategy enablement in live trading is not authorized by this task; future promotion requires independent risk/release approval.
- Cross-sectional/fundamental factor inputs require observed-time, release-time, currency, revision and security master contracts before integrating Vibe factor zoo.
- Any use of hierarchical model selection for strategies must specify whether the output is a research hint or an operator-approved strategy; this plan uses research-only behavior.
- Actual provider API credentials, upstream commercial API keys and model-weight permissions are not supplied; none are introduced here.
