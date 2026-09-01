# Recovery objectives and audit-key rotation

## RPO/RTO evidence

`RecoveryObjectiveReport` evaluates every required durable store against:

- the latest valid snapshot capture time;
- snapshot manifest identity;
- verification time;
- required off-host-copy evidence;
- the latest valid isolated restore drill;
- drill age;
- observed drill duration;
- configured maximum RPO and RTO.

Missing stores, stale snapshots, invalid snapshots, missing off-host copies, stale drills, drills that mutate source/restore state, and RPO/RTO violations fail closed.

The report is evidence only. It cannot create a backup, restore data, switch traffic, or declare a disaster.

## Audit HMAC keyring

Two configurations are supported:

### Single-key mode

```text
AUDIT_HMAC_KEY=<at-least-32-byte-secret>
```

### Rotation/keyring mode

```text
AUDIT_HMAC_KEYRING_JSON={"audit-2026-q3":"...","audit-2026-q4":"..."}
AUDIT_HMAC_KEY_ID=audit-2026-q4
```

Do not configure both modes. The keyring is bounded, key IDs are recorded in new entries/anchors, and historical entries remain verifiable while their keys are retained. Legacy sealed entries without key IDs are checked against the bounded retained keyring.

Every append verifies the pre-existing chain first. A missing historical key, invalid anchor, tampered entry, ambiguous configuration, weak key, or unknown active key ID fails closed.

## Rotation procedure

1. Verify the current chain and signed anchor.
2. Add the new key to the retained keyring.
3. Set `AUDIT_HMAC_KEY_ID` to the new key ID.
4. Restart through the controlled deployment path.
5. Append a test/operator event and verify both old and new history.
6. Retain historical keys for the full audit-retention period.
7. Remove an old key only after its covered history is no longer required and an authorised retention process has approved disposal.

The API does not rotate or disclose audit keys.
