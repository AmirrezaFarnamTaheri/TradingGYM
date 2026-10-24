# Snapshot verification and application recovery are different contracts

The current snapshot schema records hashes of sequentially copied files. Its
cross-process snapshot lock prevents concurrent snapshot operations; domain
writers do not share that lock. A snapshot can therefore pass every hash check
and still combine different domain revisions. Restore planning and the isolated
copy/re-hash drill now expose `validation_scope: byte_integrity_only` and
`application_recovery_qualified: false`. The explicit application-recovery gate
rejects all v1 snapshots, even when their byte hashes and HMAC pass.

This patch preserves v1 manifest signatures and existing byte-verification
compatibility. It does not silently upgrade old snapshots or enable online
restore. Reverting the patch removes the explicit gate and fields; it does not
alter any snapshot data or improve its consistency.

## Work required before an operational restore can be supported

1. Stop API admission and all managed workers, then stop external CLI, scheduled
   and maintenance writers. Implement participation by every writer in one
   documented offline or barrier protocol; a user checkbox or backup-only lock
   is insufficient evidence.
2. Capture the declared state set together with audit anchors, pending outboxes,
   leases, idempotency indexes and accounting events. Record schema versions and
   configuration/model dependencies without copying credentials or runtime
   authority tokens. A local filesystem snapshot is only sufficient when the
   documented filesystem and stopped-writer conditions hold for every volume.
3. Restore to an isolated fresh home with outbound execution unavailable. Verify
   hashes/chains, domain schemas and cross-store references, then start the
   application and reconcile jobs, orders and virtual accounts. Preserve unknown
   order outcomes and never resubmit them merely because a process disappeared.
4. Record a scoped startup/reconciliation receipt and operator recovery point.
   Financial state written after that point requires reconciliation; replacing
   it with an old backup is not a valid rollback.

Until those conditions have implementation and fault-injection evidence,
snapshots remain useful for byte inspection and forensic comparison only.
