# ADR-031: Playground — Counterfactual Laboratory and API Composition

**Status:** Accepted
**Date:** 2026-09-01

## Decision

The playground is the governed sandbox where strategies and arms are tested against historical data under deterministic stress — "the octopus's gym":

1. **Scenario grammar** (`trading_gym/playground/scenarios.py`). A scenario is data, not code: one canonical window + ordered declarative overlays + seed + repetitions. Overlay primitives (`gap_injection`, `stale_feed`, `liquidity_drought`; fee/latency ride the run's cost parameters) are pure functions with hard invariants: same DatetimeIndex, same columns, and full OHLCV validation (`strategy_lab.evaluator.validate_market_frame`) on every result. Randomised parameters draw from a per-repetition `numpy.Generator` seeded by `SeedSequence(seed).spawn()` — Monte-Carlo sets are reproducible from the scenario definition alone.
2. **Deterministic runner** (`trading_gym/playground/runner.py`). `PlaygroundRun = f(window, strategy, overlays, costs) → equity + trades + metrics + manifest`. No lookahead is machine-checked: the decision at bar *t* uses data ≤ *t* (the strategy grammar is rolling-window), every fill happens at bar *t+1*'s open, and the test suite enforces the lag. Costs are explicit (fee + half-spread per side; zero-fee-zero-spread shows zero bleed). The manifest pins sha256 hashes of window, strategy, and overlays — any past run is forensically reproducible.
3. **API composition** (`apps/api/authz_router.py`, `apps/api/playground_router.py`). Both routers follow the convergence-router factory pattern and inherit the gateway's API-key dependency. `POST /playground/runs` is the first full-chain consumer: gateway auth → actor authentication (`X-Actor`/`X-Actor-Key` → principal binding) → wildcard-grant scope check (`playground.run`) via `GrantLedger` → frame/strategy validation shared with the live path → deterministic run. `POST /authz/grants` enforces the ADR-028 authority ceiling at the API boundary: issuers may only delegate scopes they themselves hold (operator principals are the authority source). Fail-closed everywhere: corrupt ledger → 503 on mutations; wildcard in a check request → 422; missing scope → 403.
4. **Budget gate** (`trading_gym/agent/budget_gate.py`). The planner's `budget_check` seam is now backed by real `BudgetRepository` reservations: an arm invocation reserves its declared credits and commits on use; conflict/exhaustion/missing budget → the arm benches. Zero-cost arms pass without ledger traffic.

## Rationale

The user's directives — "a playground to test strategies on previous data under different conditions" and "the agent must be authorize-able via wildcards" — meet in one endpoint: a playground run that only executes when the caller's wildcard grants cover it, over windows stressed by declarative overlays, producing evidence-grade deterministic results. Reusing `validate_market_frame` and the `StrategySpec` grammar means what passes in the playground is the same artifact shape the live path consumes — no parallel universe of test-only strategy formats. The manifest-first design (every run pinned by hashes) exists because the roadmap's self-improvement loop (Rev 4) will consume playground evaluations as its sole evidence source; a self-revising agent requires replayability of every evaluation it ever based a decision on.

## Consequences

- **Positive.** Strategy and arm candidates get a bounded, cheap, deterministic evaluation surface; the ADR-028 chain has a proven end-to-end consumer; budget discipline is mechanically enforced rather than aspirational; overlay attribution (per-overlay `touched_columns`) prepares the Monte-Carlo attribution work.
- **Negative.** Runs are synchronous and bounded (5 000 bars inline) — large sweeps need the worker path (Phase 2); the long-only fill model (enter/exit at next open) is a simplification of `ExecutionEngine` fills, so playground P&L is directionally faithful, not broker-faithful; window data travels inline in the API v1 (lake-backed windows land with Phase 2's data fusion).
- **Risks.** Playground overfitting — strategies tuned against a fixed window; mitigated by the sealed-holdout discipline of the revision loop (ADR-032/033 work) and Monte-Carlo repetition.
- **Composition.** Fusion/planner consume `Blackboard` proposals (ADR-029/030); the revision surface (Phase 5) will call `run_playground` with sealed windows; the frontend playground panel consumes `POST /playground/runs` directly.
