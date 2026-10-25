> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# Supply-chain inventory and SLO operations

## Dependency inventory

The former custom SBOM wrapper has been retired. The authoritative dependency inputs are `poetry.lock` and `frontend/package-lock.json`; current commands are in [the validation runbook](../VALIDATION_RUNBOOK.md). Run `npm audit --prefix frontend --audit-level=high` and an available current Python advisory scanner when qualifying a release. A lockfile and immutable action pins do not establish vulnerability freedom, package signatures or reproducibility. No historical generated inventory certifies this working tree.

Production release gates should additionally include:

- vulnerability scanning against a current advisory database;
- licence review and attribution;
- package-signature or provenance verification where available;
- isolated builds with pinned indexes and network policy;
- malware and secret scanning;
- SBOM publication in a standard format when required;
- retention of lockfiles, build logs, tool versions, source hash, artifact hash, and test receipt.

## Service-level objectives

Define SLOs per user-visible or authority-critical workflow, not only per process. Recommended initial indicators include:

- API authenticated request success and latency;
- market-data freshness and sequence continuity;
- paper/live iteration success and reconciliation completion;
- order submission acknowledgement and reconciliation latency;
- experiment queue wait, lease expiry, and terminal completion;
- audit outbox delivery and ledger verification;
- feature and dataset publication completeness;
- snapshot verification and recovery-drill completion.

An SLO evaluation requires a minimum event count. A window with insufficient evidence is not healthy. Error-budget policy should govern release pace, operational risk, and investigation priority; it must not independently approve model promotion or trading.

## Drift operations

Baselines must identify their source artifact hash and the period/regime they represent. Re-baselining should be a reviewed evidence change, not an automatic way to erase alerts. Evaluate distribution shift, mean shift, and missingness together, and investigate data admission, feature code, market regime, and model behaviour before assigning causality.
