# ADR-019: Accounting, reproducibility, resilience, and assurance planes

**Status:** Accepted  
**Release:** 7.0.0  
**Decision date:** 2026-07-27

## Context

The 6.2.0 system established narrow live-trading authority, model integrity, durable governance, data admission, and runtime recovery. A third whole-system review found that several decisions still lacked first-class, independently verifiable evidence:

- portfolio state was available through execution and risk surfaces but not through one event-sourced accounting contract;
- backtest plans did not automatically bind source, seeds, dependencies, strategy, instrument, and execution-cost assumptions into one immutable manifest;
- feature drift and service reliability were observable but lacked persisted evidence contracts and error-budget semantics;
- local durable state had recovery guidance but no bounded, HMAC-verifiable snapshot and restore-planning primitive;
- dependency posture could be inspected manually but not emitted as a deterministic inventory;
- evidence from these planes was not composed into a single fail-closed qualification report.

The goal is not to create another execution or promotion authority. The new planes must remain evidence producers and must preserve the existing governance, model registry, pre-trade, and live-start barriers.

## Decision

### 1. Event-sourced accounting

Each account has one private, confined document with:

- an opening-cash map anchored into a per-account genesis hash;
- an ordered hash chain of typed cash, fill, fee, funding, and mark events;
- a semantic idempotency index keyed by `event_id`;
- Decimal materialisations for cash, fees, position quantity, average entry, realised P&L, unrealised P&L, and funding P&L;
- a recoverable audit outbox committed with each mutation.

Every read validates the chain, validates each typed payload, recomputes each payload hash, replays the complete event sequence from the anchored opening state, and compares the replay with the persisted materialisation. A matching event ID with different semantics is rejected. Broker reconciliation is a non-mutating report.

### 2. Deterministic execution research

The execution-simulation plane models fees, spread, volume participation, visible liquidity, market impact, volatility, latency, jitter, partial fills, and limit-price rejection. Base, adverse, and extreme scenarios are deterministic for the same order, policy, and seed. The plane has no network client and always reports `execution_authority_granted=false`.

### 3. Immutable reproducibility manifests

A run manifest binds:

- deterministic source-tree identity including authoritative data/model Python modules;
- complete normalised experiment configuration;
- strategy, instrument, dataset, model, report, and other artifact hashes;
- Python, NumPy, Torch, and environment seeds;
- selected dependency versions and runtime platform fingerprint;
- optional parent manifest and notes.

The bounded backtest workflow creates the manifest automatically. Repeating the same idempotent workflow reuses equivalent evidence; a conflicting replay fails instead of generating ambiguous provenance.

### 4. Drift and SLO evidence

Drift baselines persist finite-sample statistics, missingness, quantile bins, expected proportions, source hash, and policy thresholds. Evaluation reports PSI, mean-shift Z score, missingness delta, severity, and violations without granting execution authority.

SLO evaluation computes success and latency fractions, allowed failures, observed failures, and remaining error budget. Missing evidence is represented as `insufficient_data`, not success.

### 5. Verified local snapshots

Snapshots are explicit allowlists of confined, regular, non-secret state paths. Creation rejects symlinks, runtime controls, credential-like names, oversized files, excessive members, and excessive total expansion. Files and manifests are fsync-backed and atomically promoted. A configured `BACKUP_HMAC_KEY` authenticates the manifest.

The API exposes verification and a non-mutating restore plan only. Automatic restore is deliberately excluded because recovery authority, service quiescence, destination ownership, and rollback are deployment-specific.

### 6. Supply-chain inventory

The inventory hashes `pyproject.toml`, `poetry.lock`, `frontend/package.json`, and `frontend/package-lock.json`; records direct Python constraints, all Poetry-locked Python packages and artifact hashes, and locked npm components; rejects non-registry direct Python dependencies, insecure npm transport, missing npm integrity, and backend/frontend version incoherence. It is evidence, not a replacement for vulnerability scanning or signed provenance.

### 7. Cross-plane qualification

Qualification consumes a verified run manifest, drift reports, deterministic execution scenarios, account reconciliations, SLO evaluations, and audit health. Default policy requires at least one item from each evidence category; absent categories are blockers rather than vacuous success. Its output is a deterministic evidence hash plus blockers and warnings. A clear report means only `ready_for_human_review=true`; promotion and execution authority remain false.

## State and authority boundaries

- Accounting owns local expected state, not broker truth.
- Reconciliation compares expected and observed state but does not auto-repair either side.
- Simulation owns research estimates, not real venue behaviour.
- Drift and SLO reports inform review but cannot trigger live side effects.
- Snapshots preserve local evidence but cannot restore through the API.
- Qualification cannot approve, activate, reserve, spawn, or trade.
- Governance, model registry, live runtime authorisation, and pre-trade execution remain the only applicable authorities.

## Failure and recovery semantics

- Corrupt accounting chains, payloads, indexes, or materialisations fail closed.
- A durable mutation whose audit event cannot be delivered remains committed with a pending outbox; high-risk approvals are blocked until all outboxes reconcile.
- Invalid manifests or baselines cannot be loaded as evidence.
- Snapshot creation is staged and atomic; incomplete stages are non-authoritative.
- Source hashing rejects symlinks and excludes runtime state, dependencies, datasets, model artifacts, backups, and generated build output.
- Qualification treats invalid/unsealed audit state, pending events, critical drift, insufficient evidence, unreconciled accounts, rejected extreme execution, and breached SLOs as blockers.

## Consequences

The target gains stronger experiment repeatability, accounting integrity, recovery evidence, operational measurement, and review clarity. It also gains additional durable schemas and operator responsibilities. Multi-node consensus, remote object-storage backups, tax-lot accounting, venue-specific queue models, vulnerability-feed integration, and automatic disaster recovery remain separate future work and must not be inferred from this ADR.
