> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# Disaster recovery and verified snapshot runbook

This runbook covers local durable-state evidence introduced in 7.0.0. It does not authorise an automatic restore and does not replace infrastructure-level backup, encryption, replication, or incident-management procedures.

## Recovery objectives

Before deployment, operators must define and test:

- recovery time objective for API, paper, and live services;
- recovery point objective for governance, audit, model registry, accounting, datasets, and experiment state;
- which files are authoritative and which can be regenerated;
- who can stop workers, access backup keys, approve restore, and resume live operation;
- where off-host, encrypted copies are retained and how deletion/retention is governed.

The built-in snapshot service produces a verified local snapshot only. A copy remaining on the same host is not disaster recovery.

## Snapshot creation

1. Set `TRADING_GYM_HOME` to the private durable state root.
2. Configure a strong, separately protected `BACKUP_HMAC_KEY` when authenticated manifests are required.
3. Reconcile all audit outboxes and verify the audit chain before snapshotting authority-bearing state.
4. Quiesce or pause writers where a cross-file consistency point is required. Per-file atomicity does not create a distributed transaction across independent stores.
5. Submit an explicit allowlist of relative paths. Runtime controls, backup directories, environment files, token/secret/key names, symlinks, and paths outside the state root are rejected.
6. Verify the returned manifest, file count, total bytes, SHA-256 values, and HMAC status.
7. Copy the completed snapshot to the approved off-host destination using an authenticated, encrypted channel. Re-verify after transfer.

## Restore planning

The API restore-plan endpoint is read-only. It classifies every entry as create, replace, or unchanged and reports whether the snapshot verifies. It never writes destination state.

Before any restore:

1. Stop live, paper, trainer, poller, feature, and experiment workers.
2. Preserve the damaged state and logs as incident evidence.
3. Verify the selected snapshot, HMAC, and off-host transfer checksum.
4. Confirm schema and application-version compatibility.
5. Reconcile whether restoring one store without its dependent stores would violate revision, audit, model, dataset, or lease invariants.
6. Obtain explicit restore approval under the deployment’s change-control policy.
7. Restore into an isolated staging directory first.
8. Run schema, hash, audit, accounting replay, model-registry, dataset-manifest, feature-receipt, and experiment-lease verification against staging.
9. Promote with an atomic directory or service-specific migration procedure where supported.
10. Start read-only/API health functions first, then paper operation. Require a new critical proposal and live-start authorisation for real trading.

## Mandatory post-restore checks

- audit chain valid and sealed under the intended key;
- zero pending durable outboxes or an explicitly understood degraded state;
- proposal reservations and runtime authorisations reconciled;
- active model identity exists and its artifact hash verifies;
- accounting event replay matches materialised state for every account;
- broker reconciliation differences reviewed and never auto-applied;
- experiment leases reconciled and stale workers reclaimed;
- dataset manifests, lake pending markers, feature receipts, and metrics freshness valid;
- provider destinations and secret references still comply with policy;
- drift baselines and SLO windows have known provenance after the recovery point.

## Restore drills

Run a documented drill before real funds and after material schema or infrastructure changes. Record exact source version, snapshot hash, HMAC status, timings, data loss, manual interventions, failed assumptions, and corrective actions. A successful local restore does not prove off-host, region-loss, credential-loss, or multi-node recovery.

## 8.0.0 recovery-objective evidence

Use `POST /api/v1/convergence/operations/recovery-objectives/evaluate` to compare required stores with fresh, valid, off-host snapshot evidence and isolated restore-drill duration. The endpoint is non-mutating and cannot restore or fail over production. See [Recovery objectives and audit-key rotation](RECOVERY_OBJECTIVES_AND_AUDIT_KEY_ROTATION.md).
