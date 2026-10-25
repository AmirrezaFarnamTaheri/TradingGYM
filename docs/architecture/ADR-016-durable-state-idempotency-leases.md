> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-016: Preserve Distributed Semantics in Single-Node Durable Stores

**Status:** Accepted  
**Date:** 2026-07-26

## Decision

Use private local JSON/JSONL stores with inter-process file locks, fsync plus atomic replace/append, schema versions, revision compare-and-swap, semantic idempotency fingerprints, bounded payloads, and explicit leases. Keep contracts storage-agnostic so multi-node deployments can replace local files with transactional storage without changing lifecycle semantics.

## Rationale

The peer projects exposed useful job, trigger, strategy, and approval behavior but incompatible persistence models. Copying each store would create duplicated authority and inconsistent failure handling. One target-native persistence contract provides deterministic local operation and makes distributed migration requirements explicit.

## Consequences

- Local multi-process access is serialized and crash-resistant.
- Stale revisions and semantic idempotency reuse fail closed.
- Worker ownership and lease expiry are explicit.
- Local files are not a multi-node database; distributed deployment requires transactional CAS, leases, and idempotency equivalence tests.
