# Deferred review items completed — 2026-10-25

The subsequent request explicitly included funded execution, without placing trades, and supersedes the earlier three-view scope. The cockpit now has Live monitor, Experience, Strategy lab and Settings.

## Strategy Lab and exports

Import actual OHLCV CSV using columns `at,open,high,low,close,volume`, with Unix seconds and 30–2000 strictly ordered rows. There is no bundled market replay. EMA crossover and RSI reversion use existing indicator and causal next-open replay code, with fees/slippage and a hash of supplied data. ADX uses the existing optional native recurrence when available. Results are exploratory in-sample evidence, not a promoted policy or exchange-attested history. Research never submits an order or mutates an account. Experience exports actual broker/paper executions to CSV, bounded to 10,000 records; spreadsheet formulas are escaped and unknown values remain blank.

## Statistics and shadow evaluation

The statistics head remains a HOLD producer. An explicit, default-on statistics guard now converts its high-confidence risk-off evidence into zero exposure downstream of fusion. Warmup/low-confidence abstentions do not veto other heads. The user can disable this guard in Settings; models cannot change it.

Shadow evaluation uses the same Decimal PortfolioLedger transition/hash-chain implementation with disposable in-memory storage. The production account and journals keep their durable write path. Evaluation instances refuse market feeds and external heads. The 60-bar parity check compares cash, equity, fees, positions, cooldowns and halts. One measured run took 4.41 seconds with durable storage and 0.37 seconds in evaluation storage; this is a local microbenchmark, not a general performance guarantee. Training cutoff tracking is per symbol. Shadow promotions cannot activate a funded policy.

## Funded Abantether implementation

Funded execution is disabled by default. Settings requires both live operating mode and the explicit funded-order enable switch, configured credentials, per-order and cumulative buy budgets, and a spread limit. A start request must match the saved mode. This work did not enable the switch, enter credentials, start a funded agent, or send any real exchange write request.

The transport uses the official SDK's market-order endpoint and tracking-ID order reads: [SDK endpoint catalogue](https://github.com/Abantether-com/abantether-python-sdk/blob/main/abantether_python_sdk/config.py). Buy volume is quote currency; sell volume is base currency, matching the retained adapter contract. Only spot USDT markets are supported. Orders are bounded by fresh authenticated balances, fresh quotes, advertised volume ceilings, a local minimum notional, exposure, daily loss, drawdown, stop loss, spread and durable cumulative buy budget. All nonzero assets need a quote for account valuation. Sell reductions remain available after the buy budget is exhausted.

Before a POST, a tracking ID and intent are written durably, bound to the immutable decision. An acknowledgement is never reported as a fill. Unknown outcomes and open orders block further submissions; a POST is never automatically retried. Reconciliation reads the broker by the same tracking ID, validates market and side, and only imports broker-confirmed fills. Missing search results do not prove that a timed-out POST failed. Restart preserves intents and budgets. `/funded/reconcile` is read-only at the exchange; `/funded/halt` stops the local feed and latches funded entry protection. Neither action silently cancels an exchange order. No withdrawals, leverage or short borrowing were added.

Paper flatten is disabled in live mode and never closes exchange holdings. Stops act on observed bar decisions while the agent runs, not as a guaranteed exchange-side stop order. Stopping the agent does not cancel an already accepted order or continue its protection. Local tests cover terminal partial-fill/cancel import exactly once; venue-specific fee/precision/minimum semantics still require authenticated venue qualification. Ambiguous states remain blocked rather than reconstructed as invented fills. Existing external holdings without a confirmed robot cost basis block funded targeting. Each submitted intent retains its actual account risk receipt for decision inspection. Resetting a funded halt requires a stopped, flat account and reconciled terminal intents; it does not replenish the cumulative buy budget.

## Dependency remediation

The Poetry lock was regenerated with updated API, crypto, ML, data and test dependencies, plus security floors for affected transitive dependencies. The default Torch distribution is from its official CPU index; GPU training requires a separately configured compatible Torch install. The normal application uses `mlflow-skinny` for training tracking/artifacts, not an embedded MLflow server/GraphQL stack. An external MLflow server is separately managed. Locked runtime and installed-environment Python audits, plus the frontend audit, report no known vulnerabilities at this check. Black was upgraded to 26.10.1 to address the remaining development-tool advisories. A clean audit is not proof of future safety or strategy quality.

## Final local verification

The non-integration Python suite passed 3,412 tests, with 32 skipped, 5 integration tests deselected and 8 subtests passing. The frontend passed all 9 tests. The production frontend build, generated API contracts, local document links, 296 literal translation keys and installed dependency compatibility checks passed. The running Persian Strategy lab was checked in the browser with no uploaded data: replay stays disabled and no sample records appear. Restored research feature fixtures are tracked with a SHA-256 manifest; they are not used to populate product trading history.

## Qualification boundary

Implementation and local regression evidence are distinct from external qualification. Exchange connectivity, authenticated schemas, actual funded fills, external model quality, Docker networking and remote publication are not claimed as verified. The instruction was to implement funded execution **without placing trades**, so funded venue qualification is intentionally not represented as completed. No synthetic records were added to the running product.
