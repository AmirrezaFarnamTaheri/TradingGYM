# 7.0.0 third-pass scope expansion and validation map

## Scope added

The third pass extends the target beyond feature convergence and runtime hardening into seven evidence and operational planes:

1. replay-verified portfolio accounting;
2. deterministic execution-cost and partial-fill scenarios;
3. source/seed/dependency/artifact run manifests;
4. persisted feature-drift evidence;
5. service-level objectives and error budgets;
6. verified local snapshots and dependency inventory;
7. cross-plane qualification for human review.

It also re-ran the complete donor search for smaller primitives and negative lessons, expanded the REST and operator UI source surfaces, and repaired inherited test contracts that were asserting obsolete or invalid behaviour.

## Validation classes

| Claim | Evidence |
|---|---|
| Accounting chain and materialisation integrity | typed event tests, idempotency collision tests, long/short reversal tests, tamper/replay tests, API reconciliation tests |
| Simulation determinism and bounds | deterministic replay, partial/rejected fills, limit behaviour, scenario ordering, no-authority assertions |
| Manifest integrity | configuration and manifest hashes, secret redaction, persisted retrieval, tamper rejection, source-tree identity tests including authoritative data/model modules |
| Drift evidence | baseline hash, PSI/mean/missingness thresholds, insufficient-sample handling, repository CAS tests |
| Snapshot integrity | confinement, symlink and secret rejection, file/hash/HMAC verification, tamper detection, restore-plan-only tests |
| SLO semantics | healthy, at-risk, breached, and insufficient-data evaluations |
| Supply-chain policy | direct and transitive lockfile parsing, 161 Poetry artifact-hash checks, 236 npm integrity checks, non-registry dependency rejection, version-coherence test, project inventory generation |
| Qualification composition | healthy, missing-category, and multi-plane fail-closed reports, deterministic evidence hash, no promotion/execution authority |
| Product/API integration | authenticated route exposure, accounting lifecycle, manifests, drift, SLO, snapshot, supply-chain, and capability tests |

## Explicit non-claims

The release does not claim:

- broker statements are automatically authoritative or repaired;
- execution simulation predicts venue queue position or realised impact exactly;
- a run is reproducible on every hardware/driver combination merely because its manifest verifies;
- local snapshots provide off-host disaster recovery;
- dependency inventory is a current vulnerability scan;
- drift or SLO evidence proves a model is profitable or safe;
- a clear qualification report activates a model or grants execution authority;
- multi-tenant isolation, multi-region consensus, tax accounting, or regulatory reporting are complete.

See the release receipt for exact test totals, blocked dependencies, source and archive hashes, and environment-specific limitations.
