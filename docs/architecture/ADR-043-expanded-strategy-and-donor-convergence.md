# ADR-043 — Expanded strategy, market and agent donor convergence

**Date:** 2026-10-15  
**Target:** existing TradingGYM draft PR #2, branch
`audit/2026-10-11-execution-ci-hardening`  
**Status:** first functional absorption implemented; full semantic assimilation
and release qualification remain open. Funded-live authority: **NO-GO**.

## Source inventory and what its completeness means

The 17 additional ZIP files contain **4,226 file entries**, **617 distinct
archive-qualified folder paths**, **4,180 unique SHA-256 payload hashes**,
**3,113 source-code files indexed for symbols**, and **34,209 Python-AST or
lexically extracted symbol declarations**. The lexical symbol pass found
**287 cross-archive name-overlap groups**, which are *comparison candidates*,
not proof of equivalent implementation or completeness. No Python AST syntax
errors occurred among scanned Python files.

All archive members were enumerated and hashed without executing donor
startup scripts, trading engines, installers, or credentials. Archives
`jev-ultrafast-main(1).zip`, `Jev-Auto-Router-master(3).zip` and
`JevLoop-main(3).zip` are byte-for-byte identical at file-content/path
level to the versions already reviewed in ADR-037 through ADR-042.
Duplicate appearances do not justify importing a second implementation.

**Important limitation:** complete file metadata, nested-folder and
symbol enumeration do **not** establish method-by-method semantic review
or acceptance of every module. In particular, `Vibe-Trading-main.zip`
alone has **2,882 entries**, including 1,460 `agent/src` entries, 894
`agent/tests` entries, 211 `frontend/src` entries, and 92
`agent/backtest` entries. They require focused independent system-level
review, tests, interface compatibility and release decisions.

## First-order per-archive convergence matrix

| Archive | Grounded donor modules and key capabilities | TradingGYM decision |
| --- | --- | --- |
| Jev Ultrafast (1) | `jev_ultrafast/agent.py`, dynamic browser action heads | Identical earlier ZIP. No duplicate browser runtime or autonomous brokerage UI. |
| jev-tree | `src/` hierarchical taxonomy choice and overflow partitioning | Research-only future taxonomy for catalogs larger than 255 items; twelve existing strategy templates do not justify a new native model dependency. |
| typesafe-jev-workflow | `src/typesafe_ai_langgraph/` typed email intent / graph handoffs | Typed task classification concept fits offline research workflow; no direct finance/trade relevance and no auto-triggered payments. |
| skillbox | `src/skill-manifest.ts`, `src/skill-import-safety.ts`, client-scoped versioned skills | Research/agent tool catalog version and trust suggestions only. Do not install arbitrary skill scripts or remote MCP execution inside the funded-trading process. |
| JevRouter (3) | `src/` model/provider routing, gateway and control paths | Compare against existing `intelligence/model_route_preview.py`, `llm_key_pool.py` and hourly budget. No competing live provider gateway. |
| hono-jev-router (3) | `src/` Hono edge HTTP router | Preserve the existing FastAPI auth/health API; vendor-neutral route admission as design evidence only. |
| typesafe-skill-router (3) | Python typed skill-choice gateway | Offline research task routing candidate, never an executable trade authority or untrusted dynamic code import. |
| Jev Auto Router (3) | `src/route-plan.ts`, guard and verification model | Exact repeat of previously inspected snapshot. No second dynamic model selector. |
| pi-jev-router | TypeScript agent tool/model choice adapter | Developer tooling candidate; operator-authorized model fleet and constrained decision protocol already cover trading inference. |
| JevLoop (3) | `src/decide.ts`, `src/budget.ts`, bounded state/questions and events | Exact repeat of earlier snapshot; typed System-One inference is already absorbed separately. |
| AI-Trader | `research/scripts/`, `service/server/challenge_scoring.py`, `experiment_metrics.py`, `research_exports.py` | Distinct research tournaments, relative model comparison, experiment outputs and collaboration; can augment existing experiments only with sealed fixtures and no public/live account exposure. |
| tinyfinance | Rust `crates/template` and `crates/template-bus` versioning/greetings | Build/library scaffolding, not a trading, finance, backtest or model product. Explicitly exclude runtime. |
| jeeva | Rust `engine/src/`, TypeScript `api/src/funding`, `trading-sessions`, `positions` | Funding settlement, attempt/hold ledgers and readback evidence are useful validation references for existing derivatives and venue execution gates; no unreviewed Hyperliquid live port. |
| Jev Trades | Python `pipeline/paper_trader.py`, `pipeline/db.py`, Next.js desk | Paper order lifecycle and TP/SL instrumentation useful for parity tests; the donor includes SQLite DB/WAL payloads that must not be treated as code or trusted fixtures. No live broker copy. |
| jev-trade | `src/sleeves.ts`, `src/trader.ts`, `src/book.ts`, `src/trades.ts` | Per-sleeve isolation and maker/IOC execution trace inspiration; requires exchange-specific reconciliation, tick size and fill invariants before funded integration. |
| claude-trader | `src/indicators/adx.py`, `src/strategy/signal_scorer.py`, `regime.py`, `weight_profile_selector.py` | **Absorbed:** read-only causal ADX/+DI/-DI/DX indicator and two bounded research presets. Did **not** copy uncalibrated AI weight boosts, trading loop or keys. |
| Vibe-Trading | `agent/backtest/engines/*`, `loaders/*`, `optimizers/*`, `agent/src/*`, `agent/tests/*`, `frontend/src/*` | Highest-scope comparison backlog: country/venue-specific backtests, data loaders, turnover-aware portfolio optimization and execution assurance. Avoid absorbing a second monolithic app or silent credentials/permissions. |

## Implemented method and strategy integration

### Causal Wilder directional movement

New `trading_gym/strategy_lab/adx.py` defines
`compute_adx(frame, period=14, component="adx")`. It uses the
traditional smoothed TR/+DM/-DM sums initialized from the first **complete
period** of price changes, and DX/ADX with a second full-period warmup.
This is deliberately more precise than the donor's early-sample EWM
initializer: an isolated hand-computed 2-period reference exposed the
difference and passed after the Wilder seed was corrected.

Components are `adx`, `plus_di`, `minus_di`, `dx`; periods are strict
integers 2 through 10,000. Invalid component, crossed/high-low-incoherent
OHLC, nonfinite source data or numerical overflow deny computation.
Warmup remains NaN instead of being represented as a false zero-trend
observation. All values depend only on observations up to the measured bar;
this **does not** imply an exchange order can execute at the same bar-close
price.

The registered strategy grammar has an ADX indicator with a strict
`component` enumeration; the strategy warmup estimator reserves
`2 * period` observations rather than one period.

### New declarative, research-only presets

- `adx_directional_trend`: ADX strength >25, +DI greater than -DI and
  price above EMA50 for entry; inverse DI or weakening ADX for exit.
- `adx_donchian_breakout`: close crosses above the *previous*
  20-candle channel high, with ADX and +DI confirmation; exit on a
  previous-window low-channel breach or weak ADX.

Both use existing `StrategySpec`, `ConditionGroup`, `RiskSpec` and
`Operator`, with risk fractions <=0.04 and explicit stops. They are
examples for research/backtesting, **not signal performance claims** and
not automatically wired to orders, provider presets, strategy promotion or
capital authorization. A synthetic regime-reversal fixture produces both
an entry and exit under the stipulated indicator predicates.

### Market-frame chronological precondition

The existing strategy evaluator accepted shuffled, duplicated and missing
market timestamps. This can corrupt rolling windows and crossover
decisions. `validate_market_frame` now denies these for a
`pandas.DatetimeIndex`, preserving ordered timestamped frames and
legacy RangeIndex tests. Deeper venue/event-time attestation and
non-timestamped frame-order semantics remain separate work.

### Verification scope

An isolated Python 3.13 source mirror ran a RED-to-GREEN sequence:

- 14 tests initially failed against an unimplemented indicator; 14 passed
  after the first causal implementation.
- A hand-worked Wilder seed check failed against early EWM initialization,
  then passed with the strict Wilder recurrence.
- Three bad timestamp cases initially passed incorrectly; a chronological
  integrity guard corrected them.
- **19/19** focused ADX and timestamp tests passed together; additional
  direct numeric smoke checks showed both proposed regime strategies can
  produce entries and exits on a channel-breaking price series.

These are **not** results of the full pinned TradingGYM environment.
The exact-PR-head Python 3.11/3.12 suites, TypeScript UI, Rust,
strategy Pydantic grammar integration, full pipeline backtest,
OpenAPI regeneration, source-manifest verification, and broker
qualification remain external acceptance gates.

## Second-order convergence: candidate product planes

1. **Multi-venue research fabric.** Compare Vibe's 15+ backtest engine
   families and loader-specific point-in-time handling against the existing
   `market/data_router.py`, `market/catalog.py` and strategy lab. First
   prove cashflow conventions, corporate actions, sessions, bid/ask costs,
   funding and venue calendars using shared sealed fixtures; no wholesale
   loader import.
2. **Strategy evidence and comparison plane.** Combine the new causal
   indicators with AI-Trader's controlled experiment scores and TradingGYM's
   existing purged walk-forward, bootstrap uncertainty and governance. Hold
   out complete regimes, account for multiple testing and shadow portfolios,
   and require independently recorded fill/fee assumptions.
3. **Portfolio construction and execution parity.** Vibe's turnover-aware
   optimizer and jev-trade/jeeva/Jev Trades order traces should be benchmarked
   against the existing `portfolio/rebalance.py`,
   `institutional_os/portfolio_construction.py`, and
   `risk/derivatives.py` before integrating new execution algorithms.
4. **Research agent skill capability plane.** Unify versioned skills,
   source-digested tool manifests, deterministic model routing and explicit
   grants. Avoid speculative browser/shell/remote-tool permissions inside
   live trading. Jev-tree overflow partitions are optional only if the
   bounded catalog actually exceeds a model's typed-option limit.
5. **Operator product planes.** Compare the donor trading dashboards and
   collaboration scorecards to existing TradingGYM tabs. Adopt only unique
   evidence-rich workflows and role-scoped observability; never display
   an unverified simulated fill as reconciled live execution.
6. **Promotion and capital control.** No donor agent, score, conviction
   multiplier or dynamic model switch can authorize funded-live trading.
   Require separate independently verified broker settlement/recovery,
   gross-flat emergency invariants, model calibration and deployment grants.

**Disposition:** PR #2 remains draft and funded-live **NO-GO**. New source
files and templates are research/advisory only. Metadata and symbols are
fully indexed for the 17 provided archives; comprehensive semantic
acceptance of their many executable components remains unproven.
