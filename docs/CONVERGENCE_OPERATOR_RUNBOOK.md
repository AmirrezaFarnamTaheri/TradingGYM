# Convergence Operator Runbook

## Initial setup

1. Use Python 3.10-3.12, matching `pyproject.toml`.
2. Install locked Python dependencies and frontend dependencies.
3. Set a private durable directory with `TRADING_GYM_HOME`.
4. Generate independent `API_KEY`, `WORKER_API_KEY`, `APPROVAL_API_KEY`, and `AUDIT_HMAC_KEY` values.
5. Configure provider records with environment-variable names only.
6. Start the API on `127.0.0.1` unless protected by TLS, authentication, and network policy.
7. Check `GET /api/v1/convergence/capabilities` and `GET /api/v1/convergence/governance/audit/verify`.

## Daily checks

- Audit status is `valid=true` and, before high-risk work, `sealed=true`.
- No unexpected experiments remain `running` beyond their lease.
- Failed/stale jobs have an understood owner and retry disposition.
- Dataset records are active, complete enough for their use, and not quarantined.
- Provider status checks resolve only approved public destinations.
- Pending/approved proposals have valid owners, summaries, expiry, and risk tiers.
- Trigger evaluations are not producing repeated proposals outside their cooldown/evaluation-key contract.
- Existing execution and reconciliation dashboards show no unknown side effects.

## Strategy-to-backtest workflow

1. Save or select a declarative strategy.
2. Resolve the instrument through the canonical registry.
3. Validate market, timeframe, date range, and warm-up bars.
4. Create `/workflows/backtest`; record the returned experiment ID and audit entry.
5. Claim the job with a worker identity and `X-Worker-Key`.
6. Heartbeat before lease expiry.
7. Complete with bounded result metadata or fail with a classified, bounded error.
8. Review walk-forward, purge/embargo, randomized-baseline, DSR, bootstrap, and FDR evidence.
9. Promote only to paper/shadow evaluation; never treat a completed research job as execution authority.

## High-risk proposal procedure

1. Verify the audit ledger and signed anchor.
2. Confirm the proposal operation, payload identity, risk tier, expiry, and current revision.
3. Independently verify the evidence and existing execution controls.
4. Approve using the human actor identity and `X-Approval-Key`.
5. Consume once immediately before the intended operation.
6. Re-run existing pre-trade and venue controls.
7. Record and reconcile the external execution receipt.

## Incident procedures

### Invalid audit ledger

- Stop approvals and consumption.
- Preserve `audit.jsonl`, its anchor, proposal state, logs, and the runtime environment.
- Do not repair by deleting or rewriting lines.
- Determine whether the issue is corruption, truncation, wrong key, or unauthorized mutation.
- Restore/reconcile from trusted evidence; verify before reopening authority.

### Stale or abandoned experiment

- Let the repository classify lease expiry on read/list/claim.
- Reclaim only when attempts remain.
- A final expired lease becomes failed and requires a new explicit request.
- Do not reuse an idempotency key for different configuration.

### Provider circuit or unsafe destination

- Disable/delete the provider record or correct it using the current revision.
- Verify the hostname resolves only to approved public addresses.
- Inspect DNS, proxy, firewall, and certificate behavior.
- Do not weaken private-address checks to restore service.

### Dataset quality failure

- Quarantine the record with a reason and expected revision.
- Preserve content and schema hashes.
- Re-fetch through a validated provider and register a new record; use supersession rather than mutation of evidence.

### Proposal/external-state disagreement

- Stop new consequential operations for the affected scope.
- Treat broker/exchange state as unknown until queried through the existing execution reconciliation path.
- Preserve proposal, audit, request ID, idempotency key, and external receipt evidence.
- Never infer success from an approved or consumed proposal alone.

## Backup and restore

- Snapshot `TRADING_GYM_HOME` while writes are quiesced or through filesystem-level atomic snapshotting.
- Store the audit sealing key separately from the state backup.
- After restore, verify JSON schemas, audit chain/anchor, proposal revisions, leases, trigger pending proposals, and dataset hashes.
- Expire/reconcile leases and approvals that crossed the restore boundary before resuming work.
