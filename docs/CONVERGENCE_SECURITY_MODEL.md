# Convergence Security Model

## Assets

- exchange and provider credentials;
- capital, positions, orders, and execution authority;
- strategy definitions and experiment results;
- market datasets and provenance;
- proposal/approval state;
- worker leases and job results;
- audit history and sealing key;
- operator identity and API keys.

## Adversaries and failures considered

- unauthenticated or under-authorized API callers;
- prompt/file/provider content attempting to become instructions;
- malicious or compromised workers;
- SSRF, DNS rebinding, redirect abuse, and proxy-environment abuse;
- secret insertion into payloads, logs, or durable state;
- stale revisions, duplicate deliveries, replayed idempotency keys, and lease theft;
- malformed, oversized, out-of-order, duplicated, or incomplete market data;
- overfit strategies and false discoveries;
- audit mutation or truncation;
- crashes between trigger evaluation and proposal persistence;
- partial external side effects and uncertain reconciliation.

## Controls

| Boundary | Controls |
|---|---|
| REST gateway | inherited `X-API-Key`, schema validation, bounded query/body fields |
| Experiment workers | independent `X-Worker-Key`, lease ownership, expiry, heartbeat, revision CAS |
| High-risk authority | independent `X-Approval-Key`, HMAC-sealed audit prerequisite, one-time consumption |
| Provider networking | HTTPS-only configurable endpoints, host/path validation, DNS resolution immediately before request, private/reserved address rejection, redirects disabled, environment proxies ignored, bounded timeout |
| Secrets | environment references, recursive redaction, no provider secret values in records |
| Agent/tool surface | allowlisted typed tools, no live-order tool, no `eval`, `exec`, shell, or subprocess path |
| Strategy input | declarative grammar, forbidden extras, typed indicators/conditions/risk settings |
| State | local file locks, fsync plus atomic replace, revisions, request fingerprints, bounded payloads |
| Audit | SHA-256 hash chain, optional HMAC per entry, signed head anchor, explicit chain-only versus sealed status |
| Market data | canonical instruments, request bounds, finite/ordered/unique OHLCV validation, hashes, provenance, quarantine |
| Statistical claims | walk-forward, purge/embargo, randomized baselines, DSR, bootstrap, FDR controls |

## Fail-closed rules

- Missing `WORKER_API_KEY` causes worker transitions to return `503`.
- Missing `APPROVAL_API_KEY` causes high/critical approval or consumption to return `503`.
- Wrong secondary keys return `403`.
- High/critical approval fails when the audit ledger is chain-only, partially sealed without a valid anchor, truncated, or invalid.
- Alias collisions, stale revisions, idempotency semantic mismatches, lease mismatch/expiry, invalid lifecycle transitions, and unsafe provider destinations are rejected.
- Invalid provider data does not become accepted data merely because failover is exhausted.

## Residual risks

- Local JSON stores are not suitable for concurrent multi-node writers without a transactional replacement.
- DNS validation reduces SSRF risk but production deployments still require network egress policy and firewall enforcement.
- A valid approval can authorize a bad decision; human process and existing execution risk controls remain necessary.
- HMAC sealing detects tampering by parties without the key; it does not provide public non-repudiation or protect a host where both data and key are compromised.
- External exchanges and providers can return inconsistent or delayed state; reconciliation remains mandatory.
- Statistical controls reduce, but cannot eliminate, non-stationarity and selection bias.

## Deployment requirements

1. Bind locally or place behind authenticated TLS termination.
2. Generate four independent secrets: gateway, worker, approval, and audit HMAC.
3. Restrict `TRADING_GYM_HOME` to the service account and back it up atomically.
4. Restrict outbound network access to approved provider and exchange destinations.
5. Separate worker and operator identities; do not expose worker credentials to browsers.
6. Monitor audit verification, stale jobs, provider circuits, proposal expiry, and reconciliation failures.
7. Keep live trading disabled until the inherited execution stack is validated in the supported environment.

## 8.0.0 authority additions

See [Actor identity and separation of duties](ACTOR_IDENTITY_AND_SEPARATION_OF_DUTIES.md), [Data lifecycle, credential rotation, and tenant isolation](DATA_LIFECYCLE_CREDENTIAL_ROTATION_AND_TENANT_ISOLATION.md), and [Recovery objectives and audit-key rotation](operations/RECOVERY_OBJECTIVES_AND_AUDIT_KEY_ROTATION.md). High-risk authority is principal-bound, audit history supports bounded key rotation, and the deployment is explicitly single-tenant per process/data home.
