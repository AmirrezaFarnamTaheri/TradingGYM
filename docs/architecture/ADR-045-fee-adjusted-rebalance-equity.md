> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-045 — Fee-adjusted rebalance equity and concentration accounting

**Date:** 2026-10-15  
**Implementation:** draft PR #2, `audit/2026-10-11-execution-ci-hardening`  
**Authority:** non-mutating portfolio planning only; funded-live **NO-GO**.

## Donor convergence context

The next integration review compared `Vibe-Trading`'s
`agent/backtest/optimizers/turnover_aware.py`,
`agent/src/portfolio/rebalance.py` and
`agent/tests/test_rebalance_execution_evidence.py` against TradingGYM's
existing `trading_gym/execution/portfolio/rebalance.py`. The donor distinguishes
target changes from actual rebalance fills, includes turnover penalties
in research, and refuses to represent stale or unpriced portfolio
snapshots as complete. TradingGYM already owns non-authoritative
`RebalancePlan` contracts, minimum cash, turnover, target limits and
estimated costs, so importing another trading engine is unnecessary.

The comparison uncovered a narrower **risk-control defect in TradingGYM**:
the planner computed projected position weights, cash weights and target
residuals relative to the portfolio's original **pre-fee** equity.
Its projected cash already deducted fees, so the reported weights
were inconsistent with the resulting portfolio balance sheet.

### Reproduced counterexample

Input: 100 USD cash, a zero-unit asset at 1 USD/unit, a desired 90%
allocation, maximum permissible asset weight 95%, 1-unit lot, and
estimated costs of 1,000 bps (10%) per traded notional.

Prior calculation:

- Buy 90 units for 90 USD, estimated fee 9 USD.
- Final cash 1 USD, actual projected equity 91 USD.
- Reported position weight 90/100 = **90%** and feasible=true.

Correct pro forma calculation:

- Position weight = 90/91 = **98.901%**, exceeding the 95% ceiling.
- Reported cash weight = 1/91, not 1/100.
- `maximum_weight:ABC-USD` enters the blocker list and feasible=false.

A second failure case demonstrated that a 100%-cost liquidation
could reduce pro forma equity to zero while the old code returned a
nominally feasible plan.

## Implemented repair

`plan_rebalance` now computes `total_estimated_cost` across the
already-validated and quantity-rounded trade candidates and derives
`projected_equity = equity - total_estimated_cost`. It rejects
non-finite or non-positive projected equity. It uses that same positive
denominator for every `PlannedRebalanceTrade.projected_weight`,
`RebalancePlan.projected_cash_weight`, target residual and
maximum-weight blocker. The existing `equity_base` remains the
**pre-trade** reported account equity to avoid changing its contract;
turnover continues to use that same initial equity denominator.

No broker call, mutation, hidden estimate, live grant, dynamic strategy
selection, or non-deterministic execution path was introduced.
This is a conservative dry-run planning correction, not proof that
the estimated 10% fee represents actual venue execution costs.

## RED-to-GREEN and evidence

The exact GitHub `rebalance.py` blob was placed in an isolated
Python 3.13.5 source mirror and its Git blob digest independently
matched **8c9e37a549f7eb46b6ba41f70d5ef0588f998463**.

The focused regression command
`python -m pytest -q tests/portfolio/test_post_fee_rebalance.py`
was first observed **RED: 2 failed, 1 passed**, specifically the
pre-fee cash-ratio mismatch and absent zero-equity denial.
After the minimal source correction it was **GREEN: 3 passed**.
An additional two-asset test verified that all projected
post-fee asset weights plus the projected cash weight sum to one,
within Decimal precision. The final focused run passed **4/4**.

Three existing portfolio behavior scenarios were reproduced in the
local mirror (sell-before-buy/no-authority, determinism/lot increments,
turnover limit/invalid targets) and passed **3/3**.
The portfolio plus paired-research selection passed **34/34**.
Finally, `python -m pytest -q` across the entire available
isolated source mirror passed **62/62**, and `compileall` passed.

The corrected GitHub blob was independently confirmed identical
to the tested source mirror:
**dfd5868b730b88a0f9282dc12f3759d928ef9914**.
The updated module SHA-256 was registered in
`docs/contracts/module_accountability.json`:
**91fa8481d3fd796e02023d15e165d849cce1d71bbda394b0171df571626c9b2a**.

These test results are **not** the complete pinned TradingGYM repository,
Windows, React, Rust, source-manifest generator, or authenticated broker
settlement test matrix. A matching file hash proves that this *module*
was tested verbatim, not that every module it interacts with was tested.

## Second-order remaining convergence

1. **Pro forma balance-sheet plane:** reconcile beginning account state,
   planned trade notional, fees, projected cash and projected equity
   with signed actual exchange fills before ever promoting a paper plan.
2. **Market snapshot evidence:** reject incomplete/stale/unpriced
   holdings and FX quotes at a separate authenticated snapshot gate,
   preserving quote-currency units and as-of timestamps.
3. **Requested-versus-executed turnover:** keep planned turnover
   distinct from realized rebalance fills, bars, cancellations and fees
   as emphasized by Vibe's execution-evidence tests.
4. **Portfolio optimization:** compare turnover penalty, group caps and
   venue constraints to existing institutional construction semantics
   using sealed cross-venue cost and holdout fixtures.
5. **Independent financial authority:** broker readback, charge/funding
   reconciliation, idempotency fencing, cash solvency and emergency
   gross-flat verification remain independent conditions.

**Disposition:** draft PR #2; no merge/release recommendation or funded-live
execution eligibility is implied.
