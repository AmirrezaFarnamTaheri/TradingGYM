# ADR-044 — Turnover-aware research portfolio construction

**Date:** 2026-10-15. **Target:** TradingGYM draft PR #2.
**Status:** implemented in research plane, **not** broker/rebalance authority.

## Source-level convergence decision

The Vibe-Trading donor's
`agent/backtest/optimizers/turnover_aware.py` has a distinct capability:
a mean-variance objective penalized by full absolute changes from prior
signed holdings, with optimization caps and explicit realized-turnover
accounting. TradingGYM already has a canonical read-only
`institutional_os/portfolio_construction.py` and a separate
`portfolio/rebalance.py`. Adding a competing optimizer facade would
duplicate account/asset contracts and broaden capital authority by
accident. The selected convergence is therefore a **new method on the
existing PortfolioConstructionRequest** with an isolated numerical
solver. No donor startup engine, credentials, model selection, trading
daemon, or order gateway is imported.

## Method implemented

`ConstructionMethod.TURNOVER_AWARE` solves a bounded long-only
fully-invested portfolio problem:

```text
minimize  -mu.T @ w + lambda * (w.T @ Sigma @ w)
          + gamma * sum(abs(w - previous_w))
subject to  sum(w) == 1
            minimum_w <= w <= maximum_w
```

- Uses SciPy SLSQP with an *epigraph* auxiliary variable for each
  absolute change, a verified analytic gradient and linear
  equality/inequality constraints.
- The previous holdings are mandatory and keyed by every instrument in
  the current universe, with explicit zeros for newly introduced
  instruments. Any off-book asset entry, missing asset, negative
  holding, boolean/string, nonfinite value or allocations not summing
  to one fails validation.
- Reports **one-way turnover** as
  `0.5 * sum(abs(w - previous_w))`; the objective penalty uses the
  full absolute sum, so the associated report field is
  `turnover_regularization = 2 * gamma * one_way_turnover`.
  These values are *not* realized broker fees or slippage.
- Validates finite PSD covariance, finite expected returns, valid
  [0,1] per-name bounds and simplex feasibility. A maximum of 64
  assets, a bounded iteration budget and a solver postcondition
  protect against unbounded requests and numerically infeasible
  allocations. Failed numerical optimization **raises**, rather than
  publishing an unverified recommendation.
- The existing construction report exposes estimated turnover,
  regularization, numerical objective and a **SHA-256 digest** of
  previous holdings instead of broadcasting individual prior balances
  in the result. Existing portfolio methods remain unchanged when
  their new inputs are not used; supplying turnover-only settings to
  another method is rejected rather than silently ignored.
- No mutation is performed and
  `execution_authority_granted` remains false. This is research-only
  sizing advice: it does not reconcile fees/funding, account cash,
  short positions, exchange quantities, stop-outs or partial fills.

## Test and published contract

A focused isolated test suite (`tests/convergence/test_turnover_aware_solver.py`
has a local mirror) passed **18/18** cases: high/zero penalty,
forced-cap turnover, covariance/risk preference, malformed input,
infeasible bounds, permutation stability and input immutability.

The GitHub PR additionally includes **canonical construction-layer
integration tests** in
`tests/convergence/test_turnover_aware_portfolio_construction.py`;
these tests were *authored but not executed against the full
TradingGYM checkout* because GitHub source is accessible through the
connector while container GitHub DNS checkout was unavailable.
`tests/contracts/test_turnover_aware_schema.py` asserts that the
published OpenAPI matches the runtime Pydantic request/report schema.
The OpenAPI `ConstructionMethod` enum and new input/output fields
have been updated, but the full application-generated
`scripts/export_openapi.py --check` remains a required CI gate.
Source-accountability hashes include the new numerical module and
modified construction owner.

## Second-order integration and qualification gates

1. **Research-only construction:** old vs proposed portfolio weights,
   covariance and utility/turnover receipts are inspectable;
   objective coefficients need timeframe-aligned empirical calibration.
2. **Rebalance planning:** pass proposed weights into the existing
   `portfolio/rebalance.py`, which separately enforces currency
   conversions, lot/tick size, minimum notional and portfolio cash.
   This pass does **not** automatically connect the two.
3. **Execution and settlement:** test jev-trade sleeve isolation, jeeva
   funding/partial-fill accounting, and Vibe's country/venue adapters
   against existing readback/fee/funding reconciliation. Only
   independently verified settlement evidence can qualify the
   existing broker authority owner.
4. **Governance:** require posterior net-of-fee backtests, point-in-time
   availability, independent venue risk limits, emergency gross-flat
   tests and explicit funded-live approval. An optimizer's mathematical
   success does not confer any of these.

**Outcome:** implemented and numerically tested in isolation; full
repository test matrix, authenticated as-of input provenance, portfolio
builder integration, cross-venue economic fidelity, and funded trading
qualification are still open. PR #2 remains draft, funded-live **NO-GO**.
