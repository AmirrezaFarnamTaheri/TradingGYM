# Actor identity and separation of duties

## Threat addressed

Free-form actor labels do not prove that two decisions came from two principals. Without credential binding, one API-key holder could create and approve a critical proposal under different names. Trading Gym therefore treats actor text as display metadata and actor credentials as the authority proof for high-risk operations.

## Principal registry

`ACTOR_CREDENTIALS_JSON` contains a bounded mapping:

```json
{
  "proposal-author": {
    "secret": "a unique high-entropy value",
    "roles": ["proposer"]
  },
  "human-reviewer": {
    "secret": "a different high-entropy value",
    "roles": ["approver"]
  },
  "runtime-executor": {
    "secret": "another independent value",
    "roles": ["executor"]
  }
}
```

Supported roles are proposer, approver, executor, operator, and auditor. Secrets must be unique and must not reuse `API_KEY`, `WORKER_API_KEY`, `APPROVAL_API_KEY`, `EMERGENCY_API_KEY`, or any audit HMAC key.

The health report exposes role counts and readiness only. It never returns secrets or secret fingerprints.

## Critical workflow

1. A proposer authenticates through `X-Actor` and `X-Actor-Key` and creates a payload-bound high/critical proposal.
2. A different approver authenticates with their actor key and the independent `X-Approval-Key`.
3. The audit ledger and every control-plane outbox must be healthy and sealed.
4. An executor authenticates separately to consume the approved proposal.
5. Model activation or live start must match the approved payload and exact revision.
6. Live start also uses the single-use runtime startup barrier.

The creator cannot approve their own proposal. A name change does not bypass principal identity. Role confusion, secret reuse, absent roles, and ambiguous principal configuration fail closed.

## Live-start barrier

For live trading, approval alone does not grant the worker authority:

1. the API reserves the approved proposal;
2. a private one-use startup artefact is minted;
3. the worker starts without constructing a live execution adapter;
4. the reservation is committed to the exact process identity;
5. a PID/birth-time-bound commit artefact is published;
6. the worker consumes both artefacts before live authority is constructed.

Direct non-dry-run CLI invocation fails closed.

## Child-process isolation

General child processes do not inherit actor credentials, audit keyrings, approval keys, emergency keys, worker keys, or gateway keys. Only explicitly delegated broker/provider variables are allowed where required.

## Rotation

Actor credential values are rotated by the deployment operator, not by the evidence API. `CredentialRotationReport` can assess age, expiry, successor overlap, purpose coverage, and fingerprint reuse without receiving secret values or granting rotation/revocation authority.
