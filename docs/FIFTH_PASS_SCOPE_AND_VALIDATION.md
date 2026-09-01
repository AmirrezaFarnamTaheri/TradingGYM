# Fifth convergence pass: scope and validation

## Purpose

Trading Gym 9.0.0 extends the 8.0.0 governed trading platform with system-level evidence and coordination contracts that were not previously first-class. The pass does not create a second execution, accounting, model, policy, audit, or deployment authority. Every new component is bounded, deterministic, review-oriented, and explicitly unable to perform the operation it evaluates.

## Added responsibility planes

### Fencing-token coordination

`trading_gym.coordination.leases` maintains durable, semantically idempotent leases with monotonically increasing fencing tokens. Ownership strings alone are insufficient: a stale process retains no authority after expiry or reacquisition. Renewal, release, expiry, conflict, and tampering paths are explicit and audit-receipted.

### Deterministic event replay

`trading_gym.events.replay` verifies a single bounded event stream with unique IDs, contiguous sequence numbers, timezone-aware chronology, prior-hash continuity, and canonical payload hashes. It reconstructs evidence only; it cannot append events or mutate authoritative materialisations.

### Collateral and treasury evidence

The collateral allocator excludes encumbered and unsettled value, applies asset haircuts, liquidity thresholds and concentration caps, and reports deterministic coverage. The liquidity forecaster conserves cash by currency and value date, distinguishes committed from probable flows, requires current explicit FX, and reports reporting-currency buffers and shortfalls. Neither surface can move cash, pledge assets, or withdraw funds.

### Policy and schema governance

Policy impact compares normalized decisions rather than version metadata, checks parent lineage, sample-level drift, relaxations, tightenings, expected outcomes and high-risk mismatches. Schema compatibility evaluates required/default semantics, types, nullability, enum evolution, identifiers, event time, field meaning and data classification. Confidentiality downgrades are hard vetoes in backward, forward and full modes.

### Capacity, failover and change readiness

Capacity evidence models offered and burst load, service time, target utilisation, workers, queue limits and backpressure. Failover evidence requires current health, complete data and the latest timely sequence; a healthy but stale replica cannot be promoted. Change-control evidence requires a bounded canary, freeze compliance, governance evidence, tested rollback and healthy observation windows. These reports cannot scale, route traffic, deploy, or roll back.

### Surveillance, privacy and counterparty evidence

Surveillance produces quantity-weighted execution-quality and potential beneficial-owner pairing evidence from immutable trade IDs without declaring illegality or taking enforcement action. Privacy minimisation checks purpose, lawful basis, collection necessity, retention, access and disclosure at field level without deleting or exporting data. Counterparty analysis models netting sets, collateral haircuts, wrong-way risk, overdue receivables, disputes and limits without legally netting or transferring collateral.

### Liquidity, factor and model risk

Liquidity risk computes exit horizons and costs from quantity, participation, ADV, spread, impact, volatility, staleness and concentration. Factor risk requires a finite, symmetric, positive-semidefinite covariance matrix and reconciles factor contributions to total variance. Model challenge evidence spans independent seeds and regimes, baseline excess return, dispersion, drawdown, turnover, completeness and failures. No report can hedge, rebalance, promote, deploy or trade.

### Sequential experiment control

Sequential experiment evidence enforces predeclared looks, bounded alpha spending, sample sufficiency and correct multiple-comparison control. The Benjamini-Hochberg rule uses the largest valid rejection prefix rather than independent per-rank decisions. Evidence cannot stop a running experiment or promote its result.

### Forensics, controls, dependencies and runbooks

Forensic readiness verifies source coverage, content hashes, receipt chaining, chronology, redaction and evidence-linked timelines. Control effectiveness requires current, independently performed, sufficiently sampled test evidence and complete risk coverage. Dependency impact uses an acyclic service graph with reverse blast-radius paths, criticality, headroom and direct rollback evidence. Runbook assurance requires contiguous steps, roles, preconditions, success evidence, bounded timeouts, destructive-action rollback, high-risk role separation, rehearsal freshness and literal-secret scanning across all operator text.

## Expanded qualification

`trading_gym.assurance.expanded_qualification` binds its receipt to the full normalized evidence payload. It requires configured minimum evidence and complete timestamps for all added classes. Missing, stale, future-dated, malformed or authority-positive evidence fails closed. A passing result means only `ready_for_human_review`; it does not activate a policy or model, spend capital, route traffic, fail over, deploy, export evidence, execute a runbook, or trade.

## Validation performed

The worktree validation includes:

- the complete convergence suite;
- the complete dependency-available project matrix with the known optional-dependency collection blockers excluded and separately recorded;
- API authentication and route integration;
- deterministic randomized invariants for treasury liquidity, factor variance, schema extension and dependency graphs;
- donor hash and bidirectional evidence verification;
- Python AST and bytecode compilation;
- OpenAPI/version consistency;
- documentation link checks;
- source, secret, dynamic-execution and direct-write review;
- deterministic archive construction, complete internal manifests and exact extracted-byte regression.

Exact final totals and hashes are release artefacts, not hard-coded in this document, because changing the source invalidates them.

## Explicit limitations

This pass does not establish distributed consensus, remote failover, real counterparty legal netting, jurisdiction-specific privacy or market-abuse conclusions, production-calibrated liquidity or factor models, live capital movement, schema migration execution, control certification, forensic legal admissibility, or automated deployment. Those require deployment-specific authority, external systems and independent qualification.
