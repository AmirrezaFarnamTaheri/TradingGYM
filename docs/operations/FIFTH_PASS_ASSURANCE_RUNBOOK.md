# Fifth-pass assurance operator runbook

This runbook explains how to use the 9.0.0 evidence planes. It is descriptive and does not authorize a change.

## Before review

1. Confirm gateway authentication and principal-role readiness.
2. Verify the audit chain and reconcile every durable outbox.
3. Confirm evidence timestamps are timezone-aware and inside the configured freshness window.
4. Record exact source, model, dataset, policy and schema identities.
5. Keep credentials outside request payloads and durable review artefacts.

## Coordination and replay

- Acquire leases with a semantic idempotency key.
- Persist and pass the returned fencing token to the protected worker operation.
- Treat expiry or token mismatch as lost ownership; never retry a protected write with an old token.
- Replay event evidence only after verifying one stream, contiguous sequences, unique IDs and chain continuity.

## Capital and risk review

- Supply explicit collateral haircuts, liquidity classifications, encumbrances and settlement status.
- Provide value-dated cashflows by currency and current explicit FX quotes.
- Review counterparty limits, disputes, overdue receivables and wrong-way multipliers separately from market risk.
- Treat missing ADV, stale market data, invalid covariance, missing FX or insufficient challenge runs as blockers.

## Policy, schema and experiment review

- Compare a candidate only against its declared parent identity.
- Review every changed high-risk decision and every relaxation.
- Reject any field-classification downgrade regardless of directional compatibility mode.
- Confirm sequential looks and hypothesis families were predeclared before observing results.

## Operational readiness

- Confirm capacity headroom and backpressure under both steady and burst load.
- Reject a failover candidate behind the latest timely observed sequence.
- Require tested rollback, canary bounds, governance evidence and a healthy observation window before change review.
- Verify dependency cycles are absent and critical blast radius is within policy.
- Require a recently rehearsed runbook with distinct high-risk roles and explicit rollback for destructive steps.

## Forensic and control evidence

- Verify every item hash and custody receipt.
- Confirm sensitive evidence is redacted and timeline entries resolve to actual evidence IDs.
- Require current independent control testing, sufficient samples and complete required-risk coverage.
- Do not describe a readiness report as legal admissibility, regulatory compliance or certification.

## Qualification interpretation

A successful expanded qualification report means only that the supplied evidence is complete enough for human review. It does not approve a proposal, activate a policy, promote a model, allocate collateral, move cash, scale workers, fail over traffic, deploy a change, execute a runbook, export evidence or submit an order.
