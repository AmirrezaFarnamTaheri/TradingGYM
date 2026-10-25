> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-022: Conservative Mutation Delivery with Unknown-Outcome Escalation

**Status:** Accepted with a restricted live-venue implementation  
**Original decision:** 2026-08-24  
**Implementation clarification:** 2026-10-11

## Invariants

1. **Do not assume venue deduplication.** Every logical mutation uses one stable idempotency key. Caching the coordinator's receipt avoids repeating a known in-process result but cannot stop a duplicate after a response is lost or a different process is started.
2. **Replay only provably pre-send failures.** Bounded retries with jitter are allowed for failures that guarantee no venue-side effect. Aban's live adapter sets `allow_unknown_replay=False`. A post-send failure with an unknown outcome is never evidence of a rejected order and requires reconciliation before another spending mutation.
3. **Write an intent before any venue contact.** The shared-path `MutationOutbox.claim_pending` checks existing unresolved entries and appends a pending event under a process-safe claim lock. The outbox records a status per logical intent, not one record per network attempt. A journal error stops submission.
4. **Refuse unknown outcomes across restarts.** Pending or ambiguous intents in the durable journal block further spending attempts. The live coordinator also maintains in-process ambiguity holds. The outbox is evidence, not a source for automatic replay or a broker settlement oracle.
5. **Only reconcile from independent evidence.** The old idempotency key cannot be reused after `mark_reconciled` in the same coordinator. An explicit new logical operation requires a new key and fresh governance.
6. **Distinguish cancellations from fills.** Cancellation may still be attempted with a previous unresolved mutation, but a successful HTTP response is not proof: the SDK order details must show `CANCELED`. Full short closure requires independent position readback. Unverified short partial fills remain unresolved.
7. **Do not equate local reservations with broker custody.** `ReservationLedger` is process-local and settles the configured reservation estimate, not independently proven fills, actual venue fees, collateral, or reconciled broker balance. Multiple workers on separate roots or hosts are unsupported for funded authority.

## Failure classification

| Outcome | Automatic retry? | Durable state | Spending release |
| --- | --- | --- | --- |
| Confirmed pre-send transient error | Yes, bounded | Pending until final outcome | Only once proven terminal |
| Provably terminal local validation/authorization error | No | Terminal | Release reserved amount |
| Network request outcome unknown, incomplete fill, or unverified post-send receipt | No on Aban | Ambiguous | Hold until reconciliation |
| Confirmed fill, locally settled and journaled | No need | Committed | Finalize estimated local reservation; venue settlement remains separate |
| Journal unavailable before submission | No | Refuse to send | Release pre-send reservation if present |
| Post-submission journaling or local settlement fails | No | Pending/ambiguous | Fail closed; do not claim trade success |

## Safety boundaries and follow-up gates

The lock protects competing claims on **one shared trusted filesystem path**. It is not a distributed transaction. Live funded deployment requires a single authoritative account owner or transactional shared ledger with fencing, broker-reported actual fills/fees/collateral, recovery tests for process death and network partition, and qualified emergency gross-exposure reduction. A lease expiration or a manual journal edit must never itself authorize replaying an order with an unknown outcome.

Read-only request retry remains an independent SDK responsibility. Tests exercise pre-send classification, unknown-outcome latching, journal durability and claims, restart barriers, and independently verified cancellation/short closure. A successful unit matrix is necessary but not sufficient for real venue qualification.
