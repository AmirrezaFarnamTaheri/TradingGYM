# Trading Gym / AxiomForge 31.7.1

Trading Gym is a governed reinforcement-learning trading platform that combines:

- declarative strategy research and bounded backtesting;
- canonical instrument, dataset, and feature provenance;
- replay-verified order lifecycle, crash-recoverable order-to-accounting synchronisation, Decimal portfolio accounting, settlement, corporate actions, and explicit broker reconciliation;
- deterministic partial-fill, queue-position, slippage, fee, latency, calibration, derivatives, and multi-asset stress analysis;
- portfolio and execution risk controls;
- evidence-first research workspaces, leased experiment jobs, and immutable full-source/seed/dependency/artifact run manifests;
- persisted feature-drift baselines, SLO/error-budget evaluation, and cross-plane qualification;
- proposal-only automation and four-eyes approval;
- integrity-checked model registration and activation;
- paper and live market loops using closed-candle feature refresh;
- verified local-state snapshots, restore drills, RPO/RTO evidence, migration/version-skew planning, and complete Poetry/npm dependency inventory;
- content-addressed lineage, shared budget reservations, data-retention/legal-hold planning, credential-rotation readiness, and fresh-evidence qualification;
- principal-bound four-eyes governance, rotating audit HMAC keyrings, explicit single-tenant deployment isolation, and authenticated REST, WebSocket, monitoring, and operator surfaces;
- fencing-token coordination, deterministic event replay, collateral and treasury-liquidity planning, and shared capacity/backpressure evidence;
- policy-impact review, schema-evolution assurance, sequential experiment control, model challenge testing, and factor-risk decomposition;
- surveillance, privacy minimisation, counterparty exposure, liquidity-to-exit analysis, failover readiness, and bounded change-control evidence;
- forensic chain-of-custody, control-effectiveness review, dependency blast-radius analysis, and rehearsed rollback-aware runbook assurance;
- twenty bounded institutional intelligence planes spanning semantic mining, regimes, alpha, signal graphs, portfolio construction/twins, microstructure/routing/quality, agent workforce/memory, model lifecycle/explainability/causal research, data/feature integrity, enterprise risk, policy/compliance, distributed assurance, product collaboration, and scientific reproduction;
- replay-safe automatic mutation retry: stable idempotency keys per logical mutation, bounded backoff with jitter, transient-vs-unknown-outcome classification, coordinator ambiguity holds with fail-closed escalation, and a durable outbox journal (see ADR-022);
- statistical evidence battery: Probabilistic/Deflated Sharpe Ratio, Minimum Track Record Length, CSCV/PBO overfitting detection, BCa and moving-block bootstrap confidence intervals;
- broker-faithful simulation depth: per-symbol contract-size/margin-rate multipliers, margin-level telemetry, a lot-level position ledger feeding a graded stop-out cascade (close-worst-lot-first with minimal restoring fractions), L2 depth-walk fills in the execution engine, volume-participation caps, calibrated discrete slippage distributions, an ideal-fill upper-bound oracle, balance reservations enforced across live mutations, a long/short collateral ledger with proportional margin release (posted collateral equity-faithful), and P&L decomposition with an honest residual bucket;
- operational hardening: latching kill switch with stale-data detection, asymmetric re-entry cooldowns, timeframe-floored ATR stops with tiered ratchet take-profits, GO/NO-GO validation orchestrator, sealed experiment specs, fill reconciliation tolerances, Retry-After-aware token buckets, cross-broker order-status normalisation, lookahead-keyed feature caches, auditable pre-trade gate payloads, experience memory, capability-degradation ladders, deterministic position-limit risk gates feeding advisory LLM payloads, and no-trade trial pruning in HPO sweeps;

> **Qualification status:** source-hardened and extensively validated in the supplied environment. It is **not represented as universally production-ready**. Live exchange behaviour, credentials, deployment isolation, disaster recovery, sustained load, the complete dependency-qualified suite, and the frontend production build must be validated in the intended environment before real funds are used.

## Security and authority model

Live order authority is intentionally narrow:

1. Training produces a candidate and registers an immutable SHA-256-identified artifact.
2. Automated comparison may advance a candidate only to **Staging**.
3. Production activation requires a critical proposal, a different human approver, an independent approval credential, a healthy HMAC-sealed audit ledger, and zero degraded audit outboxes.
4. The API may start live trading only with the active admitted model.
5. Startup uses a single-use two-phase runtime barrier: proposal reservation, worker spawn, governance commit, then a PID-bound commit artifact.
6. The live worker cannot construct its execution adapter until that barrier is consumed.
7. Direct non-dry-run `apps.live` invocation is disabled. Shell access remains a privileged administrative boundary and must be restricted by the deployment.

Independent credentials are used for separate authorities:

| Credential | Authority |
|---|---|
| `API_KEY` | gateway/API access |
| `WORKER_API_KEY` | experiment-worker lease transitions |
| `APPROVAL_API_KEY` | high/critical proposal decisions |
| `EMERGENCY_API_KEY` | emergency process termination |
| `AUDIT_HMAC_KEY` or `AUDIT_HMAC_KEYRING_JSON` + `AUDIT_HMAC_KEY_ID` | authenticated audit entries, historical verification, rotation, and sealing |
| `ACTOR_CREDENTIALS_JSON` | principal-bound proposer, approver, executor, operator, and auditor roles |

Broker and provider secrets are not stored in general state. Child processes receive only explicitly delegated secret variables. Actor, audit-keyring, approval, emergency, worker, and gateway credentials are stripped unless a child has an explicit, narrow need.

The service is intentionally **single-tenant per process and `TRADING_GYM_HOME`**. Set `TRADING_GYM_DEPLOYMENT_MODE=single_tenant` and a stable `TRADING_GYM_TENANT_ID`. `TRADING_GYM_MULTI_TENANT=true`, any alternate deployment mode, or a mismatched `X-Tenant-ID` fails closed. Multi-tenant isolation is not claimed.

## Automatic mutation retry

Live venue mutations are retried automatically under ADR-022 rules:

- one stable idempotency key per logical mutation; every attempt reuses it;
- only provably pre-send network errors and explicit unknown-outcome errors are retried (bounded attempts, exponential backoff with jitter);
- the coordinator latches an ambiguous key and refuses resubmission unless the venue adapter declares `supports_idempotent_replay`; clearing requires `mark_reconciled`;
- every attempt is journalled to `TRADING_GYM_HOME/production/mutation_outbox.jsonl`; `scan_outbox()` lists intents awaiting reconciliation;
- inherently idempotent operations (`order.cancel`) never taint their key.

See `docs/architecture/ADR-022-replay-safe-automatic-mutation-retry.md` for the full decision record.

## Responsibility planes

| Plane | Owns | Does not own |
|---|---|---|
| Strategy | typed DSL, templates, revisions, validation | arbitrary code or orders |
| Market/data | canonical instruments, bounded requests, OHLCV admission, provenance | venue balances or fills |
| Feature | deterministic feature construction and receipts | repairing corrupt source data |
| Research | evidence dossiers, quality checks, workspaces, snapshots | factual truth without evidence |
| Experiment | priority, leases, retries, cancellation, reconciliation, immutable source/seed/artifact manifests | model promotion or execution |
| Accounting | opening-balance genesis, Decimal events, replay-verified cash/positions/P&L, reconciliation reports | broker mutations or fabricated fills |
| Execution research | deterministic costs, partial fills, latency and adverse scenarios | venue access or execution authority |
| Monitoring | drift baselines, SLOs, error budgets and evidence status | automatic risk acceptance |
| Resilience | HMAC-verifiable local snapshots, isolated restore drills, RPO/RTO reports, migration/version-skew plans and dependency inventory | remote backup orchestration, automatic restore or failover |
| Data governance | classification, retention windows, legal holds and purge-candidate review | deletion, archival, legal conclusions or records disposal |
| Credential governance | secret-free age, expiry, overlap, successor and fingerprint readiness | secret retrieval, generation, rotation, revocation or distribution |
| Deployment | one tenant per process and durable data home | multi-tenant routing or isolation |
| Assurance | deterministic aggregation with every configured evidence class, observation timestamps, freshness bounds and authority-negative checks | promotion, execution, deletion, rotation, restore or failover authority |
| Risk | position sizing and portfolio gates | bypassing pre-trade controls |
| Trigger/proposal | condition state and human-reviewable proposals | direct side effects |
| Model | immutable artifact admission, hashes, lifecycle and active identity | automatic Production promotion |
| Audit | ordered tamper evidence, outboxes and sealing | recovery from total host/key compromise |
| Execution | pre-trade checks, venue mutations, receipts, reconciliation and panic | donor-specific parallel execution paths |
| Coordination | fencing-token leases, expiry, renewal, release and stale-owner exclusion | distributed consensus, workload execution or authority delegation |
| Event replay | bounded chain verification, sequence integrity and deterministic reconstruction | event append, state mutation or partial-success recovery |
| Capital and treasury | collateral eligibility, haircut/concentration limits and multi-currency liquidity forecasts | transfers, pledges, withdrawals or settlement authority |
| Policy and schema assurance | decision-impact review, lineage and compatibility/security-downgrade vetoes | policy activation, schema registration or data migration |
| Operational assurance | capacity, failover, change control, dependency impact, forensics and runbook readiness | scaling, failover, deployment, evidence export or runbook execution |
| Compliance and privacy | adverse-execution/self-trade evidence and data-minimisation review | legal conclusions, enforcement, deletion or disclosure authority |
| Model and statistical assurance | sequential testing, multiple-comparison control, seed/regime challenge and factor decomposition | model promotion, trading or automated risk acceptance |
| Agent memory (ADR-034) | bounded in-session cycle history, tighten-only caution over fusion thresholds, consolidated hash-verified long-term experience journal, bounded advice digests | loosening any gate, authority, or persistence outside its own journal |
| Tool revision (ADR-034) | per-tool usage metering, reasoned runtime revision and retirement, nominations for tools the registry lacks | granting a capability, creating execution authority, or applying a nomination |
| Strategy forge (ADR-034) | grammar-bounded parameter mutation, sealed-window scoring through the playground, candidate submission to the revision gate chain | emitting code, mutating strategy structure, or approving its own candidates |
| Simulation kernel (ADR-035) | bit-identical accelerated bar loop behind an optional, parity-tested ctypes seam | new simulation semantics, I/O, randomness or any reachable authority |
| Federated tool intake | skill-package intake with risk markers, role-separated quarantine, and static assurance over hashes, forbidden capabilities, retries, timeouts and dependency DAGs | activating a skill, granting a capability, or executing remote tool code |
| Economic actions | payment-metadata normalisation (USDC transfers, Lightning invoices) and x402 challenge parsing with budget reservation | resolving secrets, signing, broadcasting, moving funds, or expanding its own payment authority |

## Important invariants

- Market mutations are retried automatically only under the classification rules of ADR-022: provably pre-send network errors, inherently idempotent operations such as order cancellation, and unknown outcomes on venues that deduplicate by idempotency key. Any other ambiguous outcome latches its key and blocks resubmission until a human reconciliation clears it.
- Withdrawal admission fails closed when network limits cannot be established.
- Live observations are rebuilt from finite, chronological, fully closed candles and act at most once per new base candle.
- Historical shards, lake partitions, features, metrics, state documents, model artifacts, and reports use bounded admission and atomic publication.
- Model deserialisation uses a private checksum-verified snapshot. MLflow deserialisation is disabled by default and requires an explicit risk acknowledgement inside an isolated worker.
- WebSocket sequence gaps, queue loss, callback failure, stale heartbeat, and reconnect exhaustion are explicit degraded states.
- High-risk authorization is blocked when any control-plane audit outbox is pending or degraded.
- UI state is never authoritative.
- Accounting materialisations are replayed from the anchored opening balance and immutable event chain on every read.
- Qualification reports require complete, timezone-aware, sufficiently fresh evidence; missing, stale, or future-dated evidence fails closed.
- Qualification may only mark a candidate ready for human review; it cannot activate models, start trading, delete data, rotate credentials, restore state, or fail over traffic.
- Legal holds override purge candidacy; immutable evidence is retained; lifecycle evaluation never mutates storage.
- Snapshot restore endpoints and recovery-objective reports are plan/evidence-only; mutation requires a separate operator-controlled recovery procedure.
- Agent memory can only tighten (ADR-034): caution deltas are non-negative and the leverage multiplier is at most 1, validated at construction, so no memory state can lower a fusion threshold or raise exposure above the uncautioned decision.
- The long-term experience journal hashes every record; an edited, reordered, or truncated journal fails the open instead of silently poisoning retrieval.
- The strategy forge mutates only inside the declarative grammar and never emits code; structure changes are S-tier revisions, and every candidate is scored by real sealed-window playground runs before the ADR-033 gate chain decides.
- Tool nomination grants nothing: a nominated tool remains uncallable until human absorption adds it, and retired tools are removed from selection rather than left callable.
- The Rust simulation kernel (ADR-035) is optional and reference-defined: absent or ABI-mismatched artifacts degrade to the Python loop, and the parity suite asserts bit-identical equity curves and trade ledgers.
- Federated tool intake stays evidence-only: every package lands quarantined with a content hash and risk markers, risky packages can never be admitted, and quarantine requires a reviewer distinct from the preparer.
- Economic actions are normalisation only: payment intents carrying secret material are rejected outright, a policy that would allow secret resolution, seed persistence, or execution is refused, and challenge plans are computed without ever granting signing or broadcast authority.

## Requirements

- Python `>=3.10,<3.13` (3.12 recommended)
- Poetry or another environment manager capable of installing `pyproject.toml`
- Node.js 18+ only for the React operator UI
- A filesystem and service account that preserve private permissions and atomic replacement semantics
- Optional: a Rust toolchain (1.80+) to build the ADR-035 simulation kernel. Without it the platform runs the Python reference loop unchanged.

## Optional: build the simulation kernel

The playground's bar loop has a bit-identical Rust implementation. It is loaded through `ctypes` if a compiled artifact is present and ignored otherwise:

```bash
cd rust/axiom_kernel && cargo build --release
```

`AXIOM_KERNEL_PATH` points the loader at a specific artifact; `AXIOM_KERNEL_DISABLE=1` forces the Python path. `trading_gym.playground.kernel.kernel_status()` reports availability plus the reason. `tests/playground/test_kernel_parity.py` asserts bitwise parity when an artifact exists and skips the comparison when it does not — the loader-contract tests always run.

## Installation

```bash
poetry install --with dev
```

Create secrets outside source control. At minimum, set a strong gateway key:

```bash
export API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
```

For governed high-risk operations also configure independent values for `APPROVAL_API_KEY`, `WORKER_API_KEY`, and `EMERGENCY_API_KEY`; configure either `AUDIT_HMAC_KEY` or a bounded `AUDIT_HMAC_KEYRING_JSON` with `AUDIT_HMAC_KEY_ID`; and configure distinct principals in `ACTOR_CREDENTIALS_JSON`. Secrets must be unique across authorities. Set `TRADING_GYM_HOME` to a private durable directory, `TRADING_GYM_DEPLOYMENT_MODE=single_tenant`, and a stable `TRADING_GYM_TENANT_ID`. The API and metrics exporter bind to loopback by default.

## Run the API

```bash
poetry run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

Do not expose the API directly to an untrusted network. Use an authenticated TLS reverse proxy, host firewall, service isolation, and egress policy.

## Train and register a candidate

The training CLI composes a base application configuration with one bounded training profile:

```bash
poetry run python -m apps.trainer.main \
  --base-config config/app.yaml \
  --config training_t4.yaml \
  --model-version candidate-2026-07-27 \
  --registry-dir checkpoints
```

Training registers a candidate but does not make it active.

## Backtest an admitted artifact

Resolve the artifact and its SHA-256 from the model registry/API, then run:

```bash
poetry run python -m apps.simulator.main run \
  --config config/app.yaml \
  --model-path checkpoints/<version>/model.zip \
  --model-sha256 <64-lowercase-hex-sha256> \
  --start-date 2024-01-01 \
  --end-date 2025-01-01 \
  --output-path reports/<unique-name>.png
```

The simulator rejects symlink-backed inputs/outputs, misaligned windows, unpinned models, and partial output publication.

## Paper and live operation

Use the authenticated API/operator UI. Paper mode uses live market/account state with a no-order adapter. Live mode additionally requires an active admitted model, critical approval, sealed audit state, a one-time reservation, and the runtime startup barrier.

The panic endpoint remains available even if audit delivery is degraded: it commits the canonical panic signal first and reports audit degradation separately. Emergency kill requires `X-Emergency-Key` and retains the panic signal unless flattening is confirmed.

## Data and feature integrity

- All requested historical shards must load; corrupt or missing shards fail the admission.
- Timestamps are UTC, ordered, bounded, and conflict-aware.
- OHLCV values must be finite and obey envelope invariants.
- Synthetic fields are explicitly marked in provenance.
- Poller writes are merged under locks and atomically replaced.
- Nested `symbol/timeframe` manifests are collision-free.
- A pending marker makes data replacement without manifest publication fail closed.
- Feature artifacts include source/config hashes and publish atomically; partial runs fail.

## Testing

Current full-suite state (Python 3.12, dependency-complete venv): **1668 passed, 3 skipped, 0 failures** (includes AXIOM-1 authz/arms/fusion/planner/playground/attribution/autonomy/revision/memory/tool-revision/forge/forge-API/kernel-parity/docs-truth/OpenAPI-currency suites; paper venue behind the adapter seam; the Rust kernel parity block runs when a compiled artifact is present). Frontend: Vitest **39/39** plus a green production Vite build.

Core hardened matrix used for this release:

```bash
python -m pytest -q \
  tests/convergence \
  tests/common/test_ipc.py \
  tests/models/test_versioning.py \
  tests/risk/test_circuit_breaker.py \
  tests/risk/test_circuit_breakers.py \
  tests/execution/test_live_execution_adapter.py \
  tests/adapters/test_enhanced_aban_client.py \
  tests/adapters/test_aban_mock.py \
  tests/adapters/test_order_status_normalization.py \
  tests/adapters/test_aban_transport_classification.py \
  tests/adapters/test_websocket.py \
  tests/convergence/test_mutation_retry.py \
  tests/execution/test_fill_models.py \
  tests/execution/test_margin_multiplier.py \
  tests/rl/test_rewards.py \
  tests/rl/test_execution_twin_oracle.py \
  tests/research/test_statistical_validation.py \
  tests/research/test_bootstrap_ci.py \
  tests/treasury/test_reservations.py \
  tests/accounting/test_pnl_decomposition.py \
  tests/risk/test_risk_absorptions.py \
  tests/features/test_feature_assembler.py \
  tests/features/test_assembler_numba.py \
  tests/monitoring/test_alerting.py \
  tests/integration/test_trainer_update.py \
  tests/pipelines/test_trainer_update.py \
  tests/api/test_api.py
```

Additional static checks:

```bash
python -m compileall -q apps trading_gym tests
poetry run ruff check apps trading_gym tests
poetry run mypy apps trading_gym
```

## Operational recovery

1. Stop authority-changing workers before repairing durable state.
2. Preserve the state directory and audit files; do not edit receipts in place.
3. Reconcile every outbox and verify the audit chain/seal.
4. Verify model artifact hashes and active-model identity.
5. Verify lake manifests, pending markers, feature receipts, metrics freshness, experiment leases, proposal reservations, order/accounting checkpoints, legal holds, credential-rotation readiness, and evidence freshness.
6. Review per-store RPO/RTO evidence and perform an isolated restore drill; do not restore through the evidence API.
7. Resume paper operation first; require a new critical approval before live operation.

## Current validation boundaries

In the supplied execution environment:

- the hardened selected suite is executed and reported in the release evidence;
- Python source compilation, API schema generation, archive safety, manifests, checksums, donor-ledger integrity, and extracted-artifact verification are executed;
- the full inherited suite runs on Python 3.12 with the complete declared dependency set: 1060 passed / 0 failures;
- native parquet, Stable-Baselines runtime, live venue/provider, sustained-load, distributed-store, and disaster-recovery behaviours are exercised by in-suite tests but remain environment-bound for production claims;
- the React operator workspace is runtime-verified: `npm run test:ci` passes 7/7 and `npm run build` succeeds.
See the peer-convergence evidence bundle and release receipt for exact commands, hashes, counts, and limitations.

## Licence

The target is distributed under `AGPL-3.0-only`. **Owner directive: ALL third-party licenses — GPL, BSD, Apache, UNLICENSED, or otherwise — are treated as MIT-equivalent.** No license-compatibility gate applies to any absorption decision. Provenance is recorded per mechanism in `docs/convergence/absorption-2026-08-24.md`. Provenance for every absorbed mechanism is recorded in `docs/convergence/absorption-2026-08-24.md`.

## 11.0.0 accessible-source saturation

The final absorption layer closes the remaining archive-visible semantic classes across the eight donor projects:

- exact reconciliation of all 2,407 surfaces;
- accountability for 22,851 extracted symbols/headings;
- accountability for 8,967 entry-point and lifecycle candidates;
- 130 donor-to-target static semantic-contract benchmarks;
- offline/online feature parity and point-in-time correctness;
- deterministic event-chain market replay;
- borrow, recall, funding and inventory review;
- accounting-reconciled performance attribution;
- temporal knowledge impact analysis;
- dependency, idempotency, approval and compensation workflow assurance;
- measured negative/recovery-path and mutation sensitivity;
- a final non-authoritative saturation gate.

“Archive saturation” applies only to the exact supplied ZIP contents. The archives contain no Git object databases or issue/PR discussions, and their full runtime dependency environments are unavailable; those limitations are reported rather than inferred away.
