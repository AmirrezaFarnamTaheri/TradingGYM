# ADR-045 — Seventeen-Donor Trading/Agent Convergence and Research Factor Plane

**Date:** 2026-10-16  
**Target:** existing draft PR #2, \`audit/2026-10-11-execution-ci-hardening\`  
**Status:** focused integrations committed; full repository and funded-live verification outstanding.  
**Authority:** research, validation, catalogue and developer tooling only. No broker orders, automatic provider activation, strategy promotion, or model-granted permissions.

## 1. Exhaustive archive-level inventory; limited semantic assurance

The 17 new ZIPs contain **4,226** accessible file entries and **3,223** source-classified entries, with **4,180** distinct SHA-256 payload hashes. Across those files, static source inspection identifies **31,916** named function/class declarations and **308** case-insensitive symbol names appearing in two or more donors. Python AST parsing reported **zero syntax failures** for the supported, size-bounded Python files. A source name and hash are evidence of identity, *not* proof of correct behavior, superior implementation, authorship, or compatible licenses.

Machine-readable companion artifacts generated from the actual archive bytes:
- \`inventory.json\`: every member and content SHA-256.
- \`modules.json\`: every source-classified file path, grouped by archive.
- \`source_symbols.json\`: exported/declared function and class names with location and content/AST hashes.
- \`all_file_convergence_register.json\`: every entry with provisional subsystem, candidate TradingGYM target, and evidence/port disposition.
- \`cross_donor_symbols.json\`: shared identifiers for human semantic triage.

These large review artifacts are linked in the ChatGPT conversation but deliberately **not committed as runtime dependencies**. The file-by-file register is a metadata coverage map, not a claim to have tested each implementation. These 17 archives are additional to the 22 previously inventoried archives in ADR-037 through ADR-044.

## 2. New-donor capability map and choices

| Archive | Grounded modules or product surfaces | Integration decision |
| --- | --- | --- |
| \`jev-ultrafast-main(1).zip\` | \`jev_ultrafast/agent.py\`, browser observation/action selection, snapshot and tool latency experiments | Retain as **offline developer/research tooling**; do not grant browser-driven agents market order submission or credentials |
| \`jev-tree-main(2).zip\` | \`src/index.ts\`, nested/partitioned typed choices, fanout cap 255, bounded calls/depth, ambiguity handling | **Adapted:** pure, deterministic hierarchical research factor catalogue; no LLM is asked to authorize or execute the selected leaf |
| \`typesafe-jev-workflow-main(1).zip\` | typed intent → downstream workflow graph with non-operative example handlers | Keep as a workflow-design pattern; use independently enforced guards before any effectful research action |
| \`skillbox-main(2).zip\` | versioned, scoped skills registry, authorization, MCP access, bundling, client UI | Do not install arbitrary third-party code into the trading runtime. Existing governed agent capabilities stay the single authority |
| \`JevRouter-main(3).zip\` | model, tool, skill, plugin and MCP candidate discovery, policy, availability, confirmation | Map to TradingGYM provider fleet, model-route previews and permission checks; **no new unchecked agent dispatcher** |
| \`hono-jev-router-main(3).zip\` | meaning-based HTTP routing via Hono and a typed classifier, plus playground | Do not route authenticated financial endpoints by model output; existing deterministic FastAPI routes remain authoritative |
| \`typesafe-skill-router-main(3).zip\` | typed tool/skill roster, caching, constrained availability | Investigate for isolated developer workflows; do not reinterpret model-suggested skills as execution grants |
| \`Jev-Auto-Router-master(3).zip\` | candidate capability discovery, route plan, policy guard, independent outcome verification | Existing \`model_route_preview.py\` already enforces explicit capability/cost/privacy admission; preserve no-authority posture |
| \`pi-jev-router-main(1).zip\` | thin agent/CLI integration and route commands | Defer, redundant with governed provider/tool graph; untrusted CLI access is not a trader privilege |
| \`JevLoop-main(3).zip\` | budgeted loop, question batching, context pruning, benchmark protocols, latency/quality evaluation | Native typed System-1 and offline evaluation already cover selected concepts; lossless provenance and authority are still required |
| \`AI-Trader-main.zip\` | experiment events and research exports, signals, challenge metrics, agent feature/network research | Existing \`experiments/peer_strategy_comparison.py\` and \`institutional_os/alpha_factory.py\` cover much of the evidence workflow; use paired, cost-adjusted evaluation for new strategy ideas |
| \`tinyfinance-main.zip\` | Rust template/event bus crates, tests, examples and distribution | Consider isolated, deterministic research event contracts, not a new order or settlement queue without Rust/browser qualification |
| \`jeeva-main.zip\` | Rust decision/backtest/funding/session engines, typed live position and order-attempt APIs, wallets and frontend | Existing TradingGYM risk/derivatives, venue and execution paths take precedence; funding cashflows and position readback require venue-specific independent reconciliation |
| \`Jev-Trades-master.zip\` | CCXT spot/paper broker, kill-switch, market feed, SQLite paper desk and TP/SL workflow | Decline verbatim import of env-driven kill-switch semantics; validate loss caps and kill evidence through TradingGYM's existing authoritative controls |
| \`jev-trade-main.zip\` | Hyperliquid book/trades, sleeves, wallet/position evidence and live desk widgets | Complement already-built book integrity and execution calibration with fixture/venue parity, not duplicate live wallets or model-controlled order entry |
| \`claude-trader-main.zip\` | Coinbase/Kraken adapters, RSI/MACD/ADX/VWAP strategies, regime profiles and multi-reviewer trade critique | **Adapt causal technical indicators and guarded research hypotheses**, not discretionary reviewer permission to trade |
| \`Vibe-Trading-main.zip\` | **484** factor-zoo source files, global equity/futures/options backtesting, market-specific loaders, portfolio optimizers, strategy discovery, swarm presets, broad UI | **Adapted:** research-only volatility regime and technical signal primitives; staged factor discovery; cross-sectional and fundamental candidates require audited point-in-time datasets, market calendar and corporate-action/funding semantics before direct execution |

The target branch already contains robust Strategy Lab templates, ADX and
Donchian channels, purged walk-forward evaluation, paired strategy replay,
portfolio rebalancing, factor-risk analytics, service/source graphs and a
multi-provider System-1 fleet. Absorbing donor code indiscriminately would
create competing authorities and double maintenance rather than convergence.

## 3. First-order implementation in this PR

### A. Causal research indicators and two strategy templates

The existing, bounded Strategy Lab now accepts:

- **\`realized_volatility\`** with \`component=volatility|percentile\`,
  \`period\` (default 14) and \`lookback\` (default 120). The first component
  is a per-bar rolling log-return standard deviation with \`ddof=0\`, **not
  automatically annualized**. The second is its strictly past/current
  rolling percentile with an exactly neutral 50 score on constant volatility.
  Full percentile warmup is \`period+lookback\` bars; a later candle cannot
  retroactively alter an earlier value.
- **\`mfi\`**: causal money-flow index from typical price and volume, bounded
  between 0 and 100. A flat price/zero-volume window is **neutral 50**,
  not a fabricated oversold or overbought reading. A strictly positive-only
  volume flow approaches 100; negative-only approaches 0. Its warmup
  accounts for the prior-bar comparison.
- **\`mfi_trend_confirmation\`**: price above EMA with MFI crossing its
  neutral level; bounded risk, stop, expiry and exit rules.
- **\`volatility_expansion_breakout\`**: realized-volatility percentile
  crossing 60, current close above a **prior-window** Donchian upper
  boundary, and MFI confirmation; explicit exits and risk limits.

All four components are **research hypotheses** in the declarative grammar.
No model checkpoint, broker, live trade mode or portfolio authority was
changed by this introduction. The reference volatility study from Vibe
uses per-market annualization assumptions; this implementation deliberately
keeps unannualized, per-bar values rather than presuming equity/crypto
timeframes are interchangeable.

The existing indicator parameter schema, warmup planner, registry and
published \`IndicatorSpec\` OpenAPI enum were updated consistently.

### B. Higher-order bounded factor discovery

The new \`strategy_lab.factor_catalogue\` module complements the repo's
versioned \`StrategyRepository\` and signal graph. It accepts a bounded
sequence of immutable \`FactorCandidate(factor_id, family, source_sha256)\`
metadata and returns a deterministic hierarchy with a 2–255 sibling cap.
Oversized branches are partitioned into stable buckets, allowing
catalogues larger than a single model-choice request.

- No expression evaluation, Python dynamic imports, source execution,
  HTTP fetch, credential handling, or brokerage operations.
- Exact candidate IDs must be unique case-insensitively; categories, paths
  and lowercase source SHA-256 references must meet bounded syntax.
- Browsing a branch returns read-only options with \`can_execute=false\`;
  a leaf is resolved **only** through a complete, explicit path.
- Catalogue content is hash-bound, and a modified/unverified tree is
  rejected before read; this **does not attest** source authorship or
  profitability.
- Synthetic tests reach 484 and 500 unique leaves with maximum 16 or
  2 options per step, respectively, without losing entries.

The public \`trading_gym.strategy_lab\` API exports this metadata-only
facility; it is not a source importer or a UI-driven strategy activator.

## 4. Verification evidence and limitations

Focused local source-mirror tests recorded RED on the missing indicator
primitives and passed **12/12** after implementation. A synthetic
closed-bar breakout fixture independently reproduced the intended event
for both new templates, with prefix invariance. The factor-catalogue
tests recorded RED before the module existed and passed **11/11**
after implementation, including large fanout partitioning and tamper
refusal.

**Important:** source-mirror results are not the exact PR-head Python
3.11/3.12 suite. Schema, full app imports, Rust and frontend builds,
broker integrations, data quality, statistical alpha, and future-market
profitability remain unverified. GitHub Actions run #38813622837
reported nine failed required jobs with **zero executed steps**, so
there is no trustworthy hosted test verdict for that head.

## 5. Second-order product planes to converge next

1. **Factor Research and Taxonomy Plane:** make candidate catalogues
   navigable in the operator research UI with query/partition evidence,
   static-factor data requirements, source digests and time-as-of contracts.
   Select ideas for testing, never directly for live execution.
2. **Point-in-Time Market/Fundamentals Plane:** add input contracts for
   ticker/security-master history, corporate actions, publish/revision
   time, market calendars, look-ahead/cross-market joins, FX conversion and
   per-asset funding. Vibe's hundreds of factors need these before parity.
3. **Multi-Market Execution Evidence Plane:** compare isolated paper/live
   observations from Jeeva, Jev Trade and Jev Trades with TradingGYM's
   single capital owner, venue receipts, fill/fee/funding reconciliation,
   kill-switch, reservation fencing and gross-flat recovery. Keep one
   operator-approved execution authority.
4. **Strategy Experiment and Model Governance Plane:** run cash-/fee-/
   slippage-adjusted paired holdouts, purged folds, multiple-testing
   correction, walk-forward regimes, and independent criticism before
   any strategy promotion. No same-model judge self-certification.
5. **Agent/Skill/Model Capability Plane:** fuse skillbox and JevRouter's
   scoped/versioned capabilities with existing explicit provider and
   tool-grant policy; no semantic HTTP dispatch for authenticated routes,
   no arbitrary shell/browser tools in funded execution workers.
6. **Operator UX Plane:** surface bounded factor browsing, research
   experiment receipts, rejection reasons, as-of provenance, execution
   reconciliation and paper/live segregation as separately navigable
   widgets backed by validated read-only APIs.
7. **Release Evidence Plane:** run all source/API/manifests/tests against
   the exact final Git SHA, with runner allocation, independent financial
   integration fixtures and fail-closed production certification.

## 6. Non-negotiable exclusions

No whole donor runtime, source-derived live order method, unaudited skill,
arbitrary model route, stored credential, third-party web search tool,
look-ahead-contaminated feature, or unverified financial execution is ported
merely because it appears useful. User-directed license assumptions do not
prove actual third-party licensing; this pass uses fresh, narrow
implementations and references the donors as design evidence.

**Release:** draft PR, research-only additions, funded-live **NO-GO**.
