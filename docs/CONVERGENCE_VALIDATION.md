# Convergence Validation Record

## Claim boundary

This release verifies the target-native convergence layer and its evidence/release integrity. It does not claim live-broker validation, external-provider correctness, distributed-store behavior, frontend production-build success, or complete inherited-suite success in the current Python 3.13 environment.

## Executed checks

| Check | Result | Scope |
|---|---:|---|
| Focused convergence tests | 43 passed | strategy, market, datasets, risk, research, providers, tools, experiments, triggers, proposals, audit, API authority, workflow composition |
| Donor archive preflight | passed | safe paths, duplicates/case collisions, symlinks/special files, CRC, bounded expansion |
| Surface inventory | 2,407 verified | every donor file hashed and classified |
| Adoption ledger | 124 decisions, 0 warnings | parent/dependency acyclicity, dispositions, target nodes, tests, bidirectional links |
| Exact donor hash verification | passed for all eight donors | each ledger slice checked against its exact extracted root |
| Evidence integrity | 2,407 hashes, 38 implemented records, 38 mapped test nodes, 0 errors | evidence-to-target ownership |
| Python compile/AST checks | passed for delivered source | syntax and forbidden dynamic-execution scan |
| Release archive verification | required at packaging | safe members, CRC, complete manifest, external checksum, independent extraction |

## Focused negative paths

The suite covers, among other cases:

- gateway authentication rejection;
- missing/wrong worker key and independent worker authority;
- missing/wrong approval key and unsealed high-risk approval;
- audit mutation, truncation, wrong HMAC key, chain-only status, and legacy-prefix sealing;
- proposal replay and invalid lifecycle transitions;
- trigger pause/cancel/cooldown/evaluation replay and exact pending-proposal recovery;
- experiment lease theft, expiry, retry exhaustion, stale reconciliation, cancellation, idempotency mismatch, priority order, and bounded results;
- provider revision conflict, invalid health path, secret-reference handling, private-address rejection, and DNS safety;
- instrument alias collision and revision conflict;
- data ordering, duplicates, invalid OHLCV, incomplete coverage, failover, and cache identity;
- workload ceiling violations;
- dataset provenance, quarantine, and workflow composition;
- strategy schema/indicator/template checks and absence of arbitrary code;
- overfitting-control calculations and purged walk-forward behavior;
- position sizing and aggregated portfolio limits;
- research evidence quality, workspace revision, and claim reflection.

## Inherited/environmental blockers

- The runtime available during convergence is Python 3.13.5; the project declares `>=3.10,<3.13`.
- Full-suite collection requires dependencies absent from this environment, including `gymnasium`, `hypothesis`, `mlflow`, and `pyarrow`.
- `pip check` reports an existing `moviepy`/`Pillow` mismatch in the host environment.
- Poetry and Python `build` are unavailable.
- Frontend `node_modules` is absent; dependency installation did not complete within the execution window, so the React production build is statically reviewed but unexecuted.

These blockers are reported separately and are not represented as passing validation.

## Production qualification still required

- clean install under Python 3.10-3.12 from the declared lockfile;
- complete inherited unit/integration suite;
- frontend install, test, and production build;
- live exchange sandbox/paper tests and reconciliation drills;
- credentialed provider tests under production DNS/egress controls;
- multi-process soak tests and, before multi-node use, transactional-store conformance tests;
- performance/resource baselines for large datasets, queues, and backtests;
- backup/restore and disaster-recovery exercise.
