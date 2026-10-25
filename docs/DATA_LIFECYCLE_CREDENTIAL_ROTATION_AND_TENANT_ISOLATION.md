> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](README.md). Historical commands, endpoints and verification counts below apply to that component only.

# Data lifecycle, credential rotation, and tenant isolation

## Data lifecycle evidence

The data-lifecycle plane accepts bounded asset evidence, explicit category rules, and legal-hold evidence. Each asset receives one review action:

- `retain` - inside the retention window or protected immutable evidence;
- `review` - the review threshold elapsed but purge is not yet eligible;
- `purge_candidate` - the configured purge threshold elapsed;
- `blocked_by_hold` - an active asset/category legal hold overrides purge eligibility.

Missing category rules, classification mismatches, disallowed personal-data flags, duplicate identities, and future-dated asset evidence fail closed.

A purge candidate is not a deletion command. The evaluator has no filesystem/database adapter and reports `deletion_authority_granted=false`, `archive_authority_granted=false`, and `legal_authority_granted=false`.

## Credential-rotation evidence

The credential evaluator receives metadata only:

- credential and key identifiers;
- one-way fingerprints;
- purpose;
- state (`active`, `retiring`, or `revoked`);
- activation/expiry timestamps;
- successor key ID;
- environment labels.

It detects missing required purposes, old/expired active credentials, reused fingerprints, missing or wrong-purpose successors, and inadequate overlap. It cannot retrieve, generate, rotate, revoke, or distribute a secret.

## Deployment isolation

The application supports one tenant per process and `TRADING_GYM_HOME`.

Required settings:

```text
TRADING_GYM_DEPLOYMENT_MODE=single_tenant
TRADING_GYM_TENANT_ID=<stable-instance-tenant-id>
TRADING_GYM_MULTI_TENANT=false
```

HTTP and WebSocket requests may include `X-Tenant-ID`; when present it must equal the configured tenant. Unsupported deployment modes, invalid IDs, explicit multi-tenant requests, and mismatched claims fail closed.

This boundary prevents accidental cross-tenant routing claims. It is **not** a multi-tenant architecture. Separate tenants require separate processes, credentials, data homes, network policies, and operational ownership.
