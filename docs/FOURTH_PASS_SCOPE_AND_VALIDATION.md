# 8.0.0 fourth-pass convergence scope and validation map

## Purpose

The fourth convergence pass treats the 7.0.0 target and every prior convergence claim as untrusted evidence. It expands the system from strong research/accounting primitives into a more complete governed trading platform while preserving one authoritative execution path.

The pass does **not** concatenate donor repositories. Donor code, tests, documents, UX, failure modes, and operational practices are decomposed into value units and reimplemented behind target-native contracts when licensing, authority, or architecture makes direct reuse inappropriate.

## Scope added or materially deepened

### Trading and portfolio semantics

- replay-verified order intent and lifecycle events;
- acknowledgements, fills, partial fills, cancellation, rejection, expiry, and broker reconciliation;
- crash-recoverable order-to-accounting checkpoints;
- FIFO, LIFO, and HIFO lot analysis with explicit fill-time FX;
- multi-currency valuation and stale/missing-rate blockers;
- calendar-aware settlement obligations and available-cash projections;
- split, dividend, symbol-change, and delisting plans;
- derivatives margin, funding, liquidation-distance, concentration, and stressed-collateral analysis;
- constraint-aware, cash-reserve-aware, increment-aware rebalance plans;
- market-session, holiday, early-close, maintenance, and overnight-session evidence.

### Research, execution, and reproducibility

- immutable full-source run manifests and parent lineage;
- content-addressed lineage DAG, ancestry, impact, and bundle export;
- shared hard/soft budget reservations and compensating backtest workflows;
- deterministic queue-position and partial-fill simulation;
- observed-fill calibration and bias reports;
- point-in-time leakage checks and complete evidence timestamps;
- migration-path and protocol/version-skew analysis.

### Governance and authority

- principal-bound proposer, approver, and executor roles;
- four-eyes checks based on authenticated principals rather than free-form names;
- independent gateway, worker, approval, emergency, actor, and audit authority;
- short-lived, single-use, PID-bound live-start authorisation;
- governed model activation and live-start operator workflows;
- audit HMAC keyring rotation with key IDs and historical verification;
- mandatory verification of the existing audit chain before append;
- explicit single-tenant-per-process/data-home enforcement;
- non-destructive data-retention and legal-hold evaluation;
- secret-free credential-rotation readiness;
- stale/missing/future evidence rejection in system qualification.

### Operations and recovery

- deterministic missed-window recurrence planning;
- replay-verified incident command, ownership, action items, postmortem, and closure gates;
- verified snapshots, isolated restore drills, and non-mutating restore plans;
- per-store RPO/RTO evaluation with off-host-copy evidence;
- build attestations, supply-chain inventory, and dependency-integrity checks;
- multi-source clock-health evaluation;
- SLO and error-budget evidence.

## Cross-plane authority rule

Evidence-producing planes may validate, compare, report, reserve, plan, reconcile, or mark a candidate `ready_for_human_review`. They cannot, by themselves:

- submit, cancel, amend, or replace a venue order;
- activate a production model;
- start live trading;
- delete or archive data;
- rotate or revoke a credential;
- restore state or fail over traffic;
- settle funds;
- apply a corporate action;
- perform tax or legal determinations;
- mutate the host clock;
- claim multi-tenant isolation.

## Qualification evidence contract

`SystemQualificationRequest` requires:

1. an explicit timezone-aware qualification time;
2. a timestamp for every required evidence class;
3. no missing evidence class;
4. no evidence older than the configured maximum age;
5. no evidence beyond the permitted future-skew window;
6. no report that grants authority outside its responsibility;
7. successful base qualification and healthy cross-plane evidence.

The resulting report contains evidence ages and remains human-review-only.

## Executed validation

Working-tree validation for this pass includes:

- 292 convergence tests;
- 575 executable project tests;
- 11 explicit dependency-qualified skips;
- 4 additional unittest subtests;
- API route and authority tests;
- replay, tamper, CAS, idempotency, stale evidence, legal-hold, credential overlap, RPO/RTO, tenant-boundary, key-rotation, and negative-authority tests.

The exact packaged-artifact totals are recorded in the release receipt after independent extraction.

## Environment blockers

Ten inherited test modules remain collection-blocked in this environment because Python 3.13 is outside the declared Python 3.10-3.12 range and optional declared dependencies such as `gymnasium`, `hypothesis`, or `pyarrow` are unavailable. These are reported separately from project regressions.

The React production build remains dependent on an npm artifact absent from the offline cache. JSX/source parsing and API-contract checks are performed, but a compiled frontend claim is not made unless the package installation succeeds.

## Explicit non-claims

8.0.0 does not claim:

- universal production readiness;
- multi-tenant service isolation;
- automatic disaster recovery;
- legal or regulatory retention compliance;
- jurisdiction-specific tax accounting;
- calibrated execution quality for every venue/asset;
- external key-management-system integration;
- live venue correctness without environment-specific credentials and tests;
- native Parquet/RL/MLflow paths not executed in the supplied environment.
