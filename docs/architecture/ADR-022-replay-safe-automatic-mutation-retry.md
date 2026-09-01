# ADR-022: Replay-Safe Automatic Mutation Retry with Ambiguity Escalation

**Status:** Accepted  
**Date:** 2026-08-24

## Decision

Governed live mutations gain automatic retry, bounded and replay-safe by construction:

1. Every logical mutation carries ONE stable idempotency key across all of its attempts. `LiveExecutionCoordinator` returns the cached receipt when a key repeats, so a retry after a lost response observes the original outcome instead of duplicating the venue order.
2. Only classified failures are retried: provably pre-send network errors (connection refused/DNS/connect timeout) and explicit `MutationOutcomeUnknown` raised by the transport. Validation, authorization, consent, and emergency-halt errors abort immediately.
3. When a submission outcome is unprovable, the coordinator records the idempotency key as ambiguous. Re-execution of an ambiguous key is refused fail-closed unless the venue adapter declares `supports_idempotent_replay`. Clearing a hold requires an explicit human `mark_reconciled`.
4. Every attempt is journalled to a durable JSONL outbox before submission; outcomes are recorded afterwards. `scan_outbox()` surfaces pending/ambiguous intents for reconciliation. The journal is evidence only — nothing is re-submitted from disk.
5. Operations whose worst-case replay is a harmless no-op (today: `order.cancel`) may keep their original error class instead of tainting the key.

## Rationale

The historical invariant — "market mutations are not automatically retried" — existed because naive retries can double orders after ambiguous network failures. Donor operational layers (kraken-cli retry taxonomy, OctoBot dual-ID order identity, longbridge Retry-After preference, QuantDinger fill-recovery tolerance) converge on the same resolution: classify failure modes precisely, key the operation idempotently, escalate exactly the unprovable case to a human, and journal everything. This ADR implements that convergence while preserving the platform's single-mutation-authority rule: retries flow through the same payload-bound coordinator consent path as first attempts.

## Consequences

- `LiveExecutionAdapter._mutate` retries transient classes automatically (bounded attempts, exponential backoff with jitter); duplicate-order risk is eliminated by key stability plus coordinator dedup.
- Venues that cannot deduplicate replays convert ambiguous outcomes into mandatory reconciliation instead of silent resubmission.
- The outbox adds one append-only file under `TRADING_GYM_HOME/production/`; operators monitor it via `scan_outbox()` and reconcile via coordinator holds.
- The old blanket prohibition invariants are replaced by the classification-based rules above; read-only request retry behaviour in `EnhancedAbanClient` is unchanged.
