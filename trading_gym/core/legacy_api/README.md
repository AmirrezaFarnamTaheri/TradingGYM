> Retained research API only. `tgym serve` mounts the new cockpit API instead. See [current documentation](../../../docs/README.md).

# Trading Gym API 11.0.0

Authenticated FastAPI control and evidence surface for Trading Gym. The API exposes bounded research, strategy, market, accounting, order, experiment, governance, assurance, recovery, and operator workflows while preserving the execution gateway as the sole venue-mutation authority.

## Start locally

```bash
export API_KEY='<strong-random-value>'
export TRADING_GYM_HOME="$HOME/.trading-gym"
export TRADING_GYM_DEPLOYMENT_MODE=single_tenant
export TRADING_GYM_TENANT_ID=default
poetry run uvicorn trading_gym.core.legacy_api.main:app --host 127.0.0.1 --port 8000
```

The default bind is loopback. Do not expose the service directly to an untrusted network. Use TLS termination, network policy, host isolation, and egress controls.

## Authentication and authority

Most HTTP requests require:

```text
X-API-Key: <gateway key>
```

WebSockets authenticate through the supported subprotocol/header mechanism; credentials are not accepted in URL query strings.

Additional independent credentials apply to specific operations:

- `X-Worker-Key` for experiment lease transitions;
- `X-Actor` and `X-Actor-Key` for principal-bound high/critical proposal roles;
- `X-Approval-Key` for high/critical approval/consumption;
- `X-Emergency-Key` for emergency process termination.

The deployment is one tenant per process and `TRADING_GYM_HOME`. An optional `X-Tenant-ID` must match `TRADING_GYM_TENANT_ID`. Multi-tenant mode is unsupported and fails closed. The API also requires a **single supervisor per local data home**: startup holds `TRADING_GYM_HOME/runtime/api-supervisor.lock` and fails if another API process owns it. Do not set multiple `uvicorn --workers` for the same data home. This is a local process-ownership constraint, not a distributed funded-trading guarantee.

## Live-trading workflow

Live start requires all of the following:

1. an active, registry-admitted, checksum-verified model;
2. a payload-bound critical proposal;
3. a distinct authenticated proposer and approver;
4. the independent approval factor;
5. a valid sealed audit ledger and zero pending/degraded outboxes;
6. an authenticated executor;
7. a one-use startup artefact and PID-bound commit barrier.

The live worker cannot construct the venue adapter before both startup artefacts are consumed. Direct non-dry-run live CLI startup fails closed.

## Convergence evidence surfaces

Selected authenticated endpoints include:

- `/api/v1/convergence/orders/*` - replay-verified order lifecycle and reconciliation;
- `/api/v1/convergence/accounting/*` - Decimal accounting, valuation, settlement, and corporate-action plans;
- `/api/v1/convergence/lineage/*` - content-addressed DAG, ancestry, impact, and bundles;
- `/api/v1/convergence/budgets/*` - bounded reservations and reconciliation;
- `/api/v1/convergence/operations/recovery-objectives/evaluate` - per-store RPO/RTO evidence;
- `/api/v1/convergence/governance/data-lifecycle/evaluate` - retention/legal-hold review;
- `/api/v1/convergence/governance/credentials/rotation/evaluate` - secret-free rotation readiness;
- `/api/v1/convergence/governance/deployment/health` - explicit single-tenant boundary;
- `/api/v1/convergence/assurance/system-qualify` - fresh, complete, authority-negative cross-plane qualification.

Evidence endpoints do not delete data, rotate secrets, restore state, fail over traffic, activate models, or trade.

## Health and readiness

- `GET /health` is a liveness probe.
- `GET /ready` validates environment, deployment isolation, and required runtime readiness.
- `GET /api/v1/status` is authenticated operational status.

## WebSockets

- `/ws/logs` streams a bounded cursor over redacted process output.
- `/ws/metrics` streams admitted metrics state.

Sequence/cursor loss, stale data, queue overflow, callback failure, and authentication failure are explicit error/degraded conditions.

## Production qualification boundary

The source has extensive executable and static validation, but the API is not represented as universally production-ready. Real broker/provider credentials, intended Python/toolchain versions, native optional dependencies, compiled frontend assets, network controls, off-host disaster recovery, sustained load, and venue-specific behaviour require environment-specific qualification.

See:

- [Security model](../../../docs/CONVERGENCE_SECURITY_MODEL.md)
- [Actor identity and separation of duties](../../../docs/ACTOR_IDENTITY_AND_SEPARATION_OF_DUTIES.md)
- [Fourth-pass validation map](../../../docs/DELETION_REVIEW.md)
- [Operator runbook](../../../docs/CONVERGENCE_OPERATOR_RUNBOOK.md)

## 9.0.0 fifth-pass evidence endpoints

All routes inherit gateway authentication. They return bounded evidence or review artefacts and do not perform the operation being evaluated.

| Route | Purpose | Explicitly absent authority |
|---|---|---|
| `GET/POST /convergence/coordination/leases` and renew/release routes | fencing-token lease evidence | workload execution or distributed consensus |
| `POST /convergence/events/replay` | deterministic chain replay | event append or materialisation mutation |
| `POST /convergence/treasury/collateral/allocate` | collateral eligibility and coverage | transfer, pledge or withdrawal |
| `POST /convergence/treasury/liquidity/forecast` | dated multi-currency liquidity | cash movement |
| `POST /convergence/governance/policies/impact` | parent-bound decision drift and relaxation review | policy activation |
| `POST /convergence/contracts/schemas/compatibility` | data/protocol compatibility and classification review | schema registration or migration |
| `POST /convergence/operations/capacity/evaluate` | worker/queue headroom and backpressure | scaling |
| `POST /convergence/operations/failover/evaluate` | health, completeness and latest-sequence readiness | traffic routing or promotion |
| `POST /convergence/operations/change-control/evaluate` | canary, freeze, governance and rollback evidence | deployment or rollback execution |
| `POST /convergence/compliance/surveillance/evaluate` | execution-quality and pairing evidence | legal conclusion or enforcement |
| `POST /convergence/privacy/minimisation/evaluate` | field-purpose and retention minimisation | deletion or disclosure |
| `POST /convergence/risk/liquidity/evaluate` | exit horizon and cost analysis | exit execution |
| `POST /convergence/treasury/counterparty/evaluate` | exposure, netting-set and collateral evidence | settlement or legal netting |
| `POST /convergence/experiments/sequential/evaluate` | alpha-spending and multiplicity evidence | stopping or promotion |
| `POST /convergence/risk/factors/evaluate` | covariance and factor contribution review | hedging or rebalancing |
| `POST /convergence/experiments/model-risk/evaluate` | seed/regime model challenge | model promotion or deployment |
| `POST /convergence/operations/forensics/evaluate` | custody, redaction and timeline readiness | evidence export/deletion or legal admissibility |
| `POST /convergence/governance/controls/evaluate` | control design and operating evidence | remediation or certification |
| `POST /convergence/operations/dependencies/impact` | transitive blast-radius evidence | deployment or scaling |
| `POST /convergence/operations/runbooks/evaluate` | role, rehearsal, evidence and rollback readiness | runbook execution |
| `POST /convergence/assurance/expanded-qualify` | full-payload-bound cross-plane review gate | approval, execution, failover, deployment or trading |

## 10.0.0 sixth-pass institutional operating-system endpoints

All routes below inherit gateway authentication and return bounded evidence, plans, simulations, or review artefacts. They do not submit orders, move funds, activate models or policies, deploy changes, execute donor code, or mutate authoritative accounting state.

| Route | Purpose | Explicitly absent authority |
|---|---|---|
| `GET/POST /institutional/artifacts` and history/reconcile routes | durable, CAS-versioned institutional reports with canonical hashes and audit replay | report approval, model promotion, or operational execution |
| `POST /institutional/source-mining/evaluate` | saturation and omission review over bounded donor-surface evidence | donor import or code execution |
| `POST /institutional/market-intelligence/regime` | deterministic market, volatility, liquidity, correlation, and suitability classification | strategy activation or trading |
| `POST /institutional/alpha-factory/evaluate` | hypothesis, evidence, review, falsification, and promotion-candidate assessment | experiment execution or promotion |
| `POST /institutional/signal-graph/analyze` | bounded acyclic signal/asset/regime/experiment knowledge-graph analysis | graph-driven trading decisions |
| `POST /institutional/portfolio/construct` | constrained institutional portfolio construction | rebalance submission or capital movement |
| `POST /institutional/portfolio/digital-twin` | bounded scenario projection and portfolio-state comparison | mutation of the real portfolio |
| `POST /institutional/execution/microstructure` | deterministic order-book, queue, fill, impact, and adverse-selection simulation | venue access or order submission |
| `POST /institutional/execution/routing` | venue-ranking and routing recommendation evidence | order routing or venue mutation |
| `POST /institutional/execution/quality` | implementation-shortfall, benchmark, venue, and slippage attribution | execution remediation |
| `POST /institutional/agents/workforce` | bounded role/task/dependency workforce plan | autonomous task execution or credential access |
| `POST /institutional/agents/memory/query` | temporally bounded, supersession-aware institutional memory retrieval | memory-authored operational decisions |
| `POST /institutional/models/lifecycle` | closed research-to-retirement lifecycle transition review | model registration, activation, or retirement |
| `POST /institutional/models/explain` | bounded feature, counterfactual, confidence, and uncertainty evidence | model inference authority or promotion |
| `POST /institutional/research/causal` | bounded causal-graph, controls, assumptions, and falsification review | causal intervention or trading |
| `POST /institutional/data-platform/evaluate` | data-domain, quality, feature-lineage, ownership, and freshness assessment | ingestion, deletion, or feature publication |
| `POST /institutional/risk/enterprise` | market, credit, liquidity, operational, and scenario risk synthesis | limit changes, liquidation, or hedging |
| `POST /institutional/governance/policy-compliance` | policy-as-code and framework/control mapping review | policy activation, certification, or enforcement |
| `POST /institutional/distributed/assure` | bounded scheduling, event-replay, lease, failure-injection, and recovery evidence | worker execution, consensus, or chaos injection |
| `POST /institutional/product/evaluate` | terminal, marketplace, collaboration, accessibility, and authority-boundary review | publication, sharing, or marketplace activation |
| `POST /institutional/scientific-lab/evaluate` | reproducibility, benchmark, strategy-genome, mutation, and scientific-review evidence | experiment execution or leaderboard publication |
| `POST /institutional/assurance/complete-convergence` | independently hash-verified twenty-domain completion gate | approval, deployment, model promotion, capital movement, or trading |

The complete-convergence result is evidence for human review only. It recomputes each domain report hash, requires all twenty independently bounded domains, and fails closed on missing, forged, stale, duplicated, or authority-bearing evidence.

## 11.0.0 final absorption endpoints

All routes inherit the gateway authentication dependency and return non-authoritative review artefacts:

- `POST /institutional/absorption/behavioural-benchmarks`
- `POST /institutional/absorption/feature-store`
- `POST /institutional/absorption/market-replay`
- `POST /institutional/absorption/financing-inventory`
- `POST /institutional/absorption/performance-attribution`
- `POST /institutional/absorption/knowledge-impact`
- `POST /institutional/absorption/workflow-assurance`
- `POST /institutional/absorption/dynamic-mutation-coverage`
- `POST /institutional/absorption/saturation`

The saturation endpoint distinguishes static contract evidence from differential runtime evidence and reports unavailable source history explicitly. It cannot trade, move capital, mutate accounting, activate policies/models, execute workflows, or deploy releases.
