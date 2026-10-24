# Capacity evidence before a storage migration

`GET /api/v1/storage/health` requires the gateway credential and reports bounded
metadata probes for known stores plus bounded accounting, default paper-account
and example-run directory inventories (at most 256 entries per inventory).
Default decision-attribution SQLite/WAL/SHM files are measured without inventing
a byte cap. Their configured limit remains unknown. Custom account/journal paths
and other domain stores are explicitly uninspected; overall coverage is partial
and requires attention even when all measured files remain below known bounds.
The 80% warning is an operational default, not a measured latency threshold.
Missing files, unreadable paths and exhausted inventories remain explicit. File
sizes do not establish chain integrity, transaction completeness or free disk.
This endpoint neither parses histories nor deletes or compacts any records.

Run `python -m scripts.benchmark_storage --records 100 1000 10000 --repeats 5`
in the installed project environment for disposable JSON read measurements.
Record hardware, filesystem, Python/dependency versions and revision alongside
the output. The experiment does not measure contention, ledger verification,
write amplification, recovery or tail latency under concurrent workers. Add
those workloads before claiming a deployment capacity or selecting a database.

Near a limit, stop admitting discretionary research jobs through the operator
workflow while preserving settlement/reconciliation and audit delivery. Archive
only after a domain-specific retention contract proves records terminal and all
references, idempotency keys, audit anchors and financial balances preserved.
No automatic retention policy or schema migration is enabled by this patch.
For a later migration, retain the old store read-only, compare record counts and
domain invariants, and make promotion a reversible reader switch. Never restore
old metadata over financial events written after the cutover.
