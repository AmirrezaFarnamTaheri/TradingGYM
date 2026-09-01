# Converged Intelligence, Market, Research, and Governance Planes

**Release:** 6.0.0 peer-converged  
**Date:** 2026-07-26  
**Authority rule:** These planes may research, validate, schedule, and propose. They do not place live orders or bypass the existing execution, pre-trade, venue, risk, emergency-stop, or reconciliation controls.

## 1. Why this architecture exists

Eight peer projects were mined at file, mechanism, test, workflow, and operational levels. Their strongest compatible semantics were reimplemented behind target-native contracts rather than copied as donor-shaped subsystems. This avoids parallel sources of truth, hidden execution authority, incompatible state models, and uncertain licence inheritance.

The convergence adds nine bounded responsibility planes:

1. **Strategy plane** — declarative strategies, templates, validation, versioned persistence, and overfitting controls.
2. **Market plane** — canonical instruments, bounded data requests, provider failover contracts, OHLCV quality checks, and a provenance-aware dataset catalogue.
3. **Risk plane** — deterministic position sizing and portfolio-level exposure gates.
4. **Research plane** — source catalogue, evidence dossiers, quality assessment, claim reflection, watchlists, notes, and report snapshots.
5. **Provider and agent plane** — provider configuration by environment-secret reference and a curated non-executing tool registry/MCP surface.
6. **Experiment plane** — durable research/backtest/discovery jobs with idempotency, priority, leases, heartbeats, retries, cancellation, and stale reconciliation.
7. **Trigger and proposal plane** — durable metric, event, and interval triggers that can create proposals only.
8. **Audit plane** — append-only hash chaining, optional HMAC authentication, and a signed head anchor.
9. **Workflow and operator plane** — cross-plane research-only backtest planning, REST routes, and a React operator panel.

## 2. Authority and trust boundaries

```text
Untrusted evidence / user inputs / provider responses
        |
        v
Pydantic schemas -> bounds -> redaction -> canonicalization
        |
        +--> research, strategy, market, risk, experiments (non-authoritative)
        |
        +--> trigger match -> operation proposal (still non-authoritative)
                                  |
                                  v
                    human decision + revision CAS
                                  |
                  high/critical: separate key + sealed audit
                                  |
                                  v
                    one-time proposal consumption
                                  |
                                  v
              existing pre-trade and execution authority
```

The following invariants are mandatory:

- Strategy expressions are declarative; arbitrary Python, shell, subprocess, dynamic evaluation, and direct live-order tools are absent from the converged planes.
- UI state, research conclusions, backtest scores, agent output, trigger matches, and approvals do not themselves execute trades.
- Every mutating durable record uses an explicit revision or idempotency contract.
- Retryable operations are bounded. Queues, payloads, output, rows, bars, time windows, lease durations, and retries have limits.
- Provider secrets are environment-variable references, never plaintext values in provider records.
- Provider health checks reject private, loopback, link-local, multicast, reserved, and metadata destinations and re-resolve immediately before access. Network egress controls remain the production backstop.
- High- and critical-risk approvals require an independent approval key and a valid HMAC-sealed audit ledger.
- Existing execution adapters remain the only path to consequential broker side effects.

## 3. Durable state ownership

By default, local state is under `TRADING_GYM_HOME` or `~/.trading-gym`:

| Owner | Durable record | Concurrency contract |
|---|---|---|
| Strategy repository | `strategies.json` | file lock, atomic replace, revision CAS |
| Instrument registry | `instruments.json` | alias uniqueness, file lock, revision CAS |
| Dataset catalogue | `datasets.json` | content identity, catalogue revision, record revision |
| Provider repository | `providers.json` | file lock, revision CAS, environment secret refs |
| Research workspace | `research_workspace.json` | file lock, atomic replace, revision CAS |
| Experiment repository | `experiments.json` | idempotency fingerprints, priority, leases, revision CAS |
| Trigger repository | `triggers.json` | evaluation keys, cooldown, pending-proposal recovery, revision CAS |
| Proposal service | `proposals.json` | idempotency fingerprints, closed transitions, one-time consume |
| Audit ledger | `audit.jsonl` plus anchor | hash chain, optional HMAC entries, signed head anchor |

These are single-node durable reference stores. File locks serialize local processes, but they are not a distributed consensus mechanism. Multi-node deployment must replace them with transactional storage while preserving the same revision, idempotency, lease, and audit contracts.

## 4. Closed lifecycles

### Experiments

```text
pending --claim--> running --complete--> succeeded
   |                   |--fail(final)--> failed
   |                   |--fail(retry)--> pending
   |                   |--lease expiry--> stale --reclaim--> running
   |                   `--final lease expiry--> failed
   `--cancel---------------------------------> cancelled
```

Rules:

- Higher numeric priority is claimed and displayed first; equal priority is FIFO by creation time.
- A worker must present `X-Worker-Key`, own the lease, use the current revision, and act before expiry.
- Semantic reuse of an idempotency key is rejected.
- Results and configuration are size-bounded and secret-redacted.

### Proposals

```text
pending --approve--> approved --consume once--> consumed
   |          |             `--expire---------> expired
   |          `--cancel-----------------------> cancelled
   `--reject----------------------------------> rejected
```

- Revisions prevent stale decisions.
- Approval quality and strategy quality are separate concerns.
- `consume` is a one-time authority token, not an execution implementation.
- High/critical decisions and consumption require `X-Approval-Key`; approvals additionally require a sealed audit ledger.

### Triggers

```text
active <--> paused
   |
   `------> cancelled (terminal)
```

A matching trigger creates or recovers exactly one proposal for an evaluation key. It never invokes an execution adapter. Metric, event, and interval trigger fields are mutually exclusive and schema-validated.

### Datasets

```text
active --> superseded
   |
   `----> quarantined
```

Dataset records bind canonical instrument, timeframe, range, provider, row counts, completeness, storage reference, schema/content hashes, warnings, and provenance metadata.

## 5. Strategy and statistical validity

The strategy grammar permits named indicators, comparison/crossover conditions, logical composition, exits, and bounded risk settings. It rejects unknown fields and executable code.

Validation includes, where applicable:

- indicator parameter and warm-up checks;
- market/timeframe/date-window workload limits;
- purged walk-forward folds and embargo periods;
- randomized baselines;
- bootstrap Sharpe uncertainty;
- deflated Sharpe assessment;
- multiple-hypothesis control using Benjamini-Hochberg false-discovery rates;
- explicit paper/shadow promotion before any existing execution pathway may consume a result.

No statistical score grants operational authority.

## 6. Market data and risk controls

The market router contract requires timezone-aware requests, canonical instruments, bounded ranges, expected-row calculation, ordered and unique timestamps, valid OHLCV relationships, finite non-negative values where applicable, content hashing, and provider-level rate/circuit state. Failover only accepts a response that passes validation.

Risk sizing supports fixed quantity, risk-per-trade, volatility targeting, and capped Kelly methods under explicit quantity, notional, and increment constraints. Portfolio evaluation aggregates duplicate instrument exposures before checking gross/net leverage, instrument and sector concentration, drawdown, and daily loss.

These controls supplement rather than replace the target's existing pre-trade validator and circuit breakers.

## 7. API surface

All 50 convergence operations are below `/api/v1/convergence` and inherit the gateway's `X-API-Key` dependency.

| Family | Routes and purpose |
|---|---|
| Capabilities | `GET /capabilities` reports supported planes and audit sealing status |
| Strategies | templates, validate, list, get, save |
| Instruments | list, resolve aliases, save with revision |
| Backtest policy | validate market/timeframe/range/warm-up workload |
| Datasets | list, register, get, quarantine |
| Workflows | create a research-only backtest job from a saved strategy and instrument |
| Risk | position sizing and portfolio gate evaluation |
| Research | source catalogue, dossier, quality, reflection, workspace, watchlist, notes, reports |
| Providers | list, save, delete, and bounded status checks |
| Experiments | list, create, get, claim, heartbeat, complete, fail, cancel |
| Agent tools | enumerate and invoke the curated registry; no direct live-trade operation |
| Triggers | list, create, change state, evaluate to proposal |
| Proposals | list, create, decide, consume |
| Audit | verify seal/chain and read a bounded tail |

Machine clients must treat HTTP `409` as a revision/idempotency conflict, `403` as invalid secondary authority, and `503` as a required security boundary not configured.

## 8. Required secrets and configuration

Generate independent high-entropy values; never reuse exchange or provider credentials:

```dotenv
API_KEY=<gateway key>
WORKER_API_KEY=<experiment worker key>
APPROVAL_API_KEY=<high-risk human approval key>
AUDIT_HMAC_KEY=<at least 32 bytes; protects entries and anchor>
TRADING_GYM_HOME=~/.trading-gym
```

Provider records store names such as `OPENAI_API_KEY`, not their values. The referenced environment variable must exist in the runtime that uses the provider.

## 9. Promotion and recovery gates

Before a strategy-derived action reaches an existing execution adapter:

1. Validate schema, canonical instrument, data identity, and workload bounds.
2. Run reproducible evaluation with declared costs, splits, leakage controls, and statistical checks.
3. Review evidence quality, limitations, and unresolved conflicts.
4. Run bounded paper/shadow execution.
5. Create a proposal with complete payload identity and expiry.
6. Apply human approval, revision CAS, separate high-risk key, and audit-seal requirements.
7. Consume approval once immediately before the consequential operation.
8. Re-run existing pre-trade, venue, balance, exposure, circuit-breaker, and kill-switch controls.
9. Emit execution receipts and reconcile external state.

On corruption or disagreement:

- stop new approvals and consumption if audit verification fails;
- stop claiming jobs if durable state cannot be read consistently;
- do not hand-edit JSON/JSONL records;
- preserve files and logs for investigation;
- restore from a known-good snapshot or reconcile through the owning service;
- verify the audit ledger before reopening authority;
- treat unknown external side effects as unresolved until reconciled through broker/execution receipts.

## 10. Validation boundary

Verified in this release:

- 43 focused convergence tests, including negative paths and API-level authority checks;
- all 2,407 donor files hashed, classified, and linked to 124 evidence decisions;
- exact donor-root hash verification and bidirectional ledger/surface validation;
- Python syntax/import-level checks for the dependency-light convergence layer;
- safe source/evidence archive structure, manifests, and checksums.

Not established in the current environment:

- the complete inherited suite under its supported Python 3.10-3.12 dependency set;
- live exchange side effects, external provider credentials, distributed-store semantics, sustained load, or failover under production networking;
- the React production build, because frontend dependencies were not available locally.

See `CONVERGENCE_VALIDATION.md`, `CONVERGENCE_SECURITY_MODEL.md`, and `CONVERGENCE_OPERATOR_RUNBOOK.md` for exact evidence and procedures.
