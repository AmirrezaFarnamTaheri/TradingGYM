# ADR-020: Fourth-pass authority, lifecycle, and recovery composition

**Status:** Accepted  
**Release:** 8.0.0

## Context

Earlier convergence releases established strong strategy, research, market-data, model, execution, accounting, and audit mechanisms. A fourth review found that platform-wide correctness still depended on implicit contracts across order lifecycle, principal identity, evidence freshness, retention, credentials, deployment topology, and recovery objectives.

## Decision

Adopt explicit target-native contracts for:

- replay-verified order lifecycle and accounting synchronisation;
- principal-bound separation of duties;
- single-use live-start authority;
- rotating audit HMAC keyrings;
- explicit single-tenant deployment isolation;
- data-retention/legal-hold evidence;
- credential-rotation readiness;
- per-store recovery objectives;
- complete, fresh cross-plane qualification evidence.

All new analytical/planning planes remain non-authoritative. The existing execution gateway remains the sole venue-mutation path.

## Consequences

### Positive

- actor-name impersonation no longer satisfies four-eyes checks;
- old or future-dated evidence cannot qualify a candidate;
- unsupported multi-tenant claims fail visibly;
- audit keys can rotate without abandoning historical verification;
- retention, legal holds, credential age, and recovery objectives become machine-readable;
- order/accounting and live-start crash gaps have explicit recovery contracts.

### Costs

- high-risk setup requires more independent credentials;
- qualification payloads are larger and timestamp-complete;
- operators must retain historical audit keys;
- each tenant requires a separate process/data home;
- recovery evidence does not replace infrastructure orchestration.

## Rejected alternatives

- free-form actor names as identity;
- one shared control credential;
- silent acceptance of stale evidence;
- pretending local JSON stores provide multi-tenant isolation;
- automatic deletion/rotation/restore through evidence endpoints;
- extending an audit chain without first validating existing history.
