# Donor absorption matrix — 2026-08-24

Second convergence pass over `to port/` — **COMPLETE**. All 109 zips deduplicated to
98 unique donors (SHA-256), extracted to `.donor_extract/` (temporary tree), and swept:
**98/98 per-repo reports** exist with file:line-cited evidence under the shared protocol.
Verdict distribution: **~30 ABSORB-PARTIAL, ~48 ABSORB-IDEAS, 24 SKIP**. This matrix
consolidates verdicts and records what was ported target-natively versus queued.

## Absorbed this pass

| Mechanism | Source evidence | Target landing | Tests |
|---|---|---|---|
| Automatic mutation retry: stable idempotency keys per logical mutation, bounded backoff+jitter, transient-vs-terminal classification | Gap vs donors (OctoBot/lumibot-style broker retry layers; kraken-cli patterns) | `trading_gym/production/mutation_retry.py`; wired into `LiveExecutionAdapter._mutate` | `tests/convergence/test_mutation_retry.py` (14 tests) |
| Ambiguous-outcome fail-closed escalation (`MutationOutcomeUnknown` + venue `supports_idempotent_replay` gate) | Derived from donor broker idempotency semantics; stricter than any donor | `trading_gym/production/runtime.py` coordinator | same file |
| Durable mutation outbox journal + `scan_outbox()` reconciliation surface | Evidence-first philosophy; donor outbox patterns | same module | journal lifecycle tests |
| Broker-style `margin_level` metric in step info/snapshot | gym-mtsim `mt_simulator.py:49-54` | `MarginTradingEnv._margin_level`, StepInfo, get_info | env logic tests |
| Pluggable reward library over episode context: log-return default, drawdown-penalised, position-tracking, decayed-unrealized-profit, event-gated profit | Gym-Trading-Env reward-over-History (`environments.py:265-267`), RLTrader WUP/IncrementalProfit designs (GPL → re-implemented from first principles, no code copied) | `trading_gym/rl/rewards.py` registry | `tests/rl/test_rewards.py` (8 tests) |
| PSR/DSR/MinTRL statistical battery (parameterised Bailey–López de Prado) | nepse-quant-terminal `validation/statistical_tests.py:27-213` (MIT) | `trading_gym/research/statistical_validation.py` | `tests/research/test_statistical_validation.py` |
| CSCV/PBO overfitting detector with sampled splits + early termination | nepse `validation/cscv_pbo.py:52-266`, corrected rank polarity per Bailey et al. 2017 | `trading_gym/research/cscv_pbo.py` | same file incl. deterministic 1.0/0.0 probes |
| BCa bootstrap confidence intervals for return statistics | pybroker `eval.py:75,152,296,628` design; nepse block-bootstrap complement | `trading_gym/research/bootstrap_ci.py` | `tests/research/test_bootstrap_ci.py` |
| Execution-engine invariant oracle under randomised trade sequences | Donor portfolio kernels used as accounting oracles (Gym-Trading-Env portfolio.py, gym-mtsim simulator) | `tests/rl/test_execution_twin_oracle.py` hypothesis suite | wealth identity, half-spread band w/ tick tolerance, no negative balances, borrow-cap ceiling |

Also fixed while absorbing: P0 inf-cap contract conflict between env and engine; P0
z-scored features leaking into execution microstructure; pandas-3 timestamp-resolution
bug in poller interval check; Starlette ≥0.46 router-wrapper breakage; pytest-9
fixture-mark collection blocker. Full detail: CHANGELOG `[31.7.1]`.

## Pending high-value candidates (awaiting sweep completion or scheduled next pass)

1. **gym-mtsim stop-out cascade** — close-most-unprofitable-first partial liquidation
   until margin restored (`mt_simulator.py:88-90`). Requires multi-lot position model;
   current net-position engine would need a graded de-risking design first. Effort L.
   **→ Landed 2026-08-25** (`margin/` lot ledger + `stop_out.py`, see fourth pass).
2. **Per-symbol margin formula** with contract_size × margin_rate / leverage
   (`mt_simulator.py:264-268`) — cross-asset fidelity for venue adapters. Effort M.
   **→ Landed in third pass** (`LeverageConfig.contract_sizes` / `margin_rates`).
3. **Long/short collateral ledger with proportional margin release**
   (ai-hedge-fund-crypto `backtester.py:111-272`) — Decimal port as treasury-plane
   oracle twin. Effort M.
4. **Optuna no-trade prune gate** (RLTrader `RLTrader.py:150-154`) — kill degenerate
   HP trials during sweeps. Effort S; integrate into experiment plane scheduler.
5. **Deterministic risk-gate node feeding advisory LLM decisions**
   (ai-hedge-fund-crypto `risk_management_node.py:37-56`) — pattern for agent-workforce
   plane proposals. Effort S.
6. **PSR/DSR/MinTRL/CSCV-PBO validation battery** (nepse-quant-terminal, per sweep
   report) — deflated Sharpe and probability of backtest-overfitting metrics for the
   **→ Landed in first pass** (`research/statistical_validation.py`, `cscv_pbo.py`).
7. **LightRAG graph-RAG ingestion/query mechanisms** — semantic-mining plane upgrade;
   evaluate against existing institutional_os catalog first.

## Second-order additions landed after the first matrix draft

- Statistical battery (PSR/DSR/MinTRL + report bundle) and CSCV/PBO detector — see
  absorbed table above; deterministic polarity probes pin PBO to exactly 0.0/1.0 on
  crafted matrices.
- BCa bootstrap confidence intervals for arbitrary return statistics.
- Kraken-taxonomy refinement to automatic mutation retry: `order.cancel` is inherently
  idempotent, so its ambiguous outcomes no longer taint the idempotency key
  (`AbanTransport.operation_is_idempotent`).
- Cross-donor margin-env gap audit closed: **no donor implements target-leverage
  actions, Decimal costs, financing accrual, and liquidation cascades together** —
  `MarginTradingEnv` leads on all four. Remaining env-side gaps are depth-walk fills,
  volume-share-calibrated slippage distributions, and an ideal-profit oracle.

## Second-wave absorptions (same pass, deeper cut)

| Capability | Source | Landing | Tests |
|---|---|---|---|
| L2 depth-walk fills; volume-participation caps; calibrated discrete slippage w/ tick rounding | TradingGym-master LOB accounting; pybroker `slippage.py:312`; futures-trading-gym calibration | `execution/fill_models.py` | `tests/execution/test_fill_models.py` |
| Balance reservation ledger (reserve cost×(1+fee), release/settle, no double-commit) | kraken-paper `lib.rs:585-592,867` | `treasury/reservations.py` | `tests/treasury/test_reservations.py` |
| P&L decomposition with honest residual bucket | kraken-replay `explain.rs:567-604` | `accounting/pnl_decomposition.py` over the real ledger | `tests/accounting/test_pnl_decomposition.py` |
| GO/NO-GO orchestrator: typed criteria, phase isolation, JSON verdict artifact | nepse `validation/run_all.py:58-835` | `operations/validation_orchestrator.py` | `tests/operations/test_validation_orchestrator.py` |
| Sealed experiment specs (name+spec+tape hash binding) | kraken `experiment.rs:178-258` | `experiments/sealed_spec.py` | same test file |
| Split-conformal VaR + coverage + budget position scale | nepse `conformal_risk.py:44-425` | `risk/conformal_var.py` | `tests/risk/test_risk_absorptions.py` |
| Latching kill switch; injected-clock staleness (donor wall-clock bug fixed) | nepse `kill_switch.py:18-141` | `risk/latching_kill_switch.py` | same file |
| Event-conditioned episode registry; forward-outcome labeling; per-bar ideal-fill oracle | trading-gym-development `tgym.py:286-335`; gym-anytrading oracle | `rl/episode_registry.py`, `rl/forward_outcomes.py` | `tests/rl/test_rl_absorptions.py` |
| Lookahead-keyed feature cache | pybroker `model.py:650-663` | `features/lookahead_cache.py` | `tests/research/test_absorption_utilities.py` |
| Auditable pre-trade gate payload, wired pre-journal in live adapter | vibe-trading-main(1) `pre_trade_risk.py:74-190` | `production/pre_trade_gate.py` + adapter wiring | `tests/research/test_absorption_utilities.py` |
| Cross-broker order-status normalization vocabulary | lumibot `entities/order.py:114-162` | `adapters/order_status_normalization.py` | `tests/adapters/test_order_status_normalization.py` |
| Capability-degradation ladder for structured LLM/tool calls | ai-trading-agent-master(1); hyperliquid TS twin | `agent/degradation_ladder.py` | `tests/agent/test_degradation_ladder.py` |
| Session-stability-aware backoff de-escalation | kraken `backoff.rs:14-44` | `production/mutation_retry.py::StabilityAwareBackoff` | `tests/research/test_absorption_utilities.py` |
| Circular moving-block bootstrap percentile CI | nepse `monte_carlo.py:159-238` complement | `research/bootstrap_ci.py::block_bootstrap_interval` | `tests/research/test_absorption_utilities.py` |

## Ranked pending candidates (consolidated from all sweeps)

**Queue empty as of 2026-08-25.** The original consolidation (14 items) plus the
four carry-over items below are all absorbed:

1. ~~Long/short collateral ledger~~ → landed (see fifth pass).
2. ~~Optuna no-trade prune gate~~ → landed (see fifth pass).
3. ~~Deterministic risk-gate node~~ → landed (see fifth pass).
4. ~~LightRAG evaluation~~ → resolved by evaluation note:
   `docs/convergence/lightrag-evaluation-2026-08-25.md` (disposition
   `reference-only`; three mechanism ideas queued for future passes if a
   retrieval-quality gap is demonstrated).

New candidates enter through donor sweeps only.

## Fourth pass — integration completions (2026-08-25)

| Capability | Source | Landing | Tests |
|---|---|---|---|
| Multi-lot position ledger (FIFO lot attribution over the net book) | gym-mtsim position model | `margin/lot_positions.py`; mirrored by `MarginTradingEnv` as an accounting twin (`_lots.apply_fill`) | `tests/margin/test_lot_positions.py` |
| Graded stop-out cascade: close worst unrealized-P&L lot first, minimal restoring fraction via bisection, terminal fallback preserved | gym-mtsim `mt_simulator.py:88-90` + kraken proportional-release semantics | `margin/stop_out.py` (engine-rule borrow rebuild: longs `max(0,\|pos\|·exec − eq)` quote, shorts `\|pos\|` base); wired into `_handle_margin_shortfall`; `LeverageConfig.stop_out_cascade` flag | `tests/margin/test_stop_out_cascade.py` |
| Depth-walk fills integrated into ExecutionEngine order path (primitive previously standalone) | TradingGym-master LOB accounting | `execution/execution_engine.py` apply-trade path incl. malformed-level validation | `tests/execution/test_engine_depth_walk.py` |
| Conformal VaR regime-weighted variant | nepse `conformal_risk.py` extension | `risk/conformal_var.py` per-regime quantiles | `tests/risk/test_conformal_regime.py` |
| BCa intervals emitted as dossier findings | pybroker eval design | `research/dossier.py` build path | `tests/research/test_bca_dossier.py` |
| Balance reservations wired through live mutations: reserve cost×(1+fee) pre-venue, settle on committed receipt, release on terminal failure, hold while ambiguous (composes with coordinator ambiguous-key hold) | kraken-paper `lib.rs:585-592,867` | `execution/live_execution_adapter.py` (`_mutate` reserve params + ledger lifecycle; all spending call sites) | `tests/execution/test_reservation_integration.py` |

Full-suite state after this pass: **1131 passed / 0 failed / 3 skipped**.

## Fifth pass — remaining open items (2026-08-25)

| Capability | Source | Landing | Tests |
|---|---|---|---|
| Long/short collateral ledger with proportional margin release; donor equity bug fixed (posted collateral counted as asset) | ai-hedge-fund-crypto `backtester.py:111-272` | `margin/collateral_ledger.py`; oracle twin of the lot ledger (net position + total marked P&L agreement, hypothesis-tested) | `tests/margin/test_collateral_ledger.py` |
| Optuna no-trade prune gate: deterministic decision surface + optuna adapter journaling `pruned_reason` user attrs; wired into HPO objective via new `EvaluationResult.trade_count` and `OptunaConfig.min_trades_per_trial` | RLTrader `RLTrader.py:150-163` | `experiments/no_trade_prune.py` + `pipelines/evaluation.py`, `pipelines/hpo.py`, `common/config.py` | `tests/experiments/test_no_trade_prune.py` |
| Deterministic position-limit risk gate feeding advisory LLM payloads (20%-of-portfolio caps, cash-bound, JSON-safe reasoning payload; no model calls) | ai-hedge-fund-crypto `risk_management_node.py:37-56` | `agent/risk_gate.py` | `tests/agent/test_risk_gate.py` |
| LightRAG graph-RAG evaluation vs institutional_os catalog — disposition `reference-only`, three mechanism ideas (dual-level fan-out, incremental hash-keyed merge, neighborhood re-rank) queued behind a demonstrated retrieval gap | LightRAG design | `docs/convergence/lightrag-evaluation-2026-08-25.md` | n/a (evaluation note) |

Full-suite state after this pass: **1152 passed / 0 failed / 3 skipped**.
Absorption queue is now empty; new candidates enter through donor sweeps only.


- Donor ledgers with look-ahead bugs are documented per report; they may be ported after correction.
- Renderer/downloader conveniences superseded by governed TradingGYM pipelines.
