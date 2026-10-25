> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# Resilience operating contract

**Scope:** TradingGYM source-hardening branch. This document describes implemented software boundaries. It is **not** a live-venue deployment certificate or an exactly-once distributed transaction guarantee.

## Availability hierarchy

| Failure domain | What continues | What stops or requires evidence | Recovery trigger |
| --- | --- | --- | --- |
| Historical market-data provider | Read-only query may use another registered, validated provider | No fabricated or malformed bars; bounded rate limits, inflight fanout and cached frame bytes | Circuit cooldown plus one fenced recovery probe, or a newly registered healthy provider |
| Primary venue **read** | Optional secondary may provide read-only tickers | Primary account mutation does **not** move to the secondary | Successful fenced primary probe, independent of past in-flight requests |
| Broker order outcome unknown | Audit, market observation and independently verified cancellation can continue | Fresh fills/new spending halt until outbox reconciliation | Human-governed reconciliation with authoritative broker data; never blind replay |
| Local treasury/authorization/position integrity | Non-authoritative monitoring may continue | New order authority refuses malformed account evidence, exhausted spend, invalid consent or denial | Verified state repair plus fresh authorization; no silent fallback |
| Optional LLM governor or prediction backend | Configured RL/rules/other advisory fallback where allowed | An explicit `PermissionError` and corrupt model observation/account context are **not** degraded into trades | Provider health restoration; preferred backends are attempted again |
| Metrics/dashboard IPC | Trading loop keeps evaluating actual account/market risk where safe | Operator health shows degraded telemetry; no fabricated positive metrics | Successful next metrics write |
| Local API supervisor | The active owner continues | A second API supervisor using the same local data directory is denied | Prior supervisor cleanly exits and releases the lock |

## Bounded read-plane behavior

`DataProviderRouter` supports the following constructor budgets:

| Parameter | Default | Purpose |
| --- | --- | --- |
| `routing_mode` | `balanced` | Default provider-selection strategy |
| `target_completeness` | `0.95` | Minimum coverage that satisfies balanced reads |
| `cache_ttl_seconds` | `30` | Lifetime of a healthy cached response |
| `max_cache_entries` | `1024` | Maximum number of retained request entries |
| `max_cache_bytes` | `268435456` (256 MiB) | Limit on estimated retained DataFrame memory, excluding transient copies |
| `max_inflight_requests` | `128` | Maximum distinct coalesced leaders at once, not an API worker/thread limit |
| `inflight_wait_seconds` | `10` | Maximum time a follower waits for a shared in-progress request |

Each `fetch(request, routing_mode=..., target_completeness=...)` may override the routing policy **without mutating the router default**. Cache keys include the selected quality policy and provider-topology revision.

- **Fast:** stop at the first valid provider, minimizing latency and provider calls even when historical coverage is partial.
- **Balanced:** continue to a better provider until the configured completeness threshold is met; retains the best valid response if none reach the threshold.
- **Quality:** inspect further eligible providers until complete historical coverage is found, prioritizing completeness over query latency and rate-limit cost.

When a provider is registered, cached selections are invalidated and a new topology revision is assigned. Old in-flight responses cannot overwrite the new revision's cache. Coalesced simultaneous callers receive independent DataFrame copies. Leader failure wakes followers; a hung external synchronous provider call itself is **not** forcibly interrupted by the router, and the owning provider adapter still requires real network timeouts.

The byte limit uses Pandas' retained-frame `memory_usage(deep=True)` estimate, **not a process RSS limit**. Load tests are required for peak temporary copies, worker counts, and Python object overhead.

## Concurrency and split-brain rules

The shared `CircuitBreaker` and the read-only `VenueRouter` use transition generations. A call admitted under an old generation may return to its own caller, but cannot flip OPEN to CLOSED, undo an operator reset, or invalidate a more recent recovery probe.

A second API process sharing a local `TRADING_GYM_HOME` cannot simultaneously supervise the same processes. This lock and the mutation outbox's shared-path file lock **do not** fence independent hosts, broker accounts or different data roots.

The production treasury's `ReservationLedger` serializes threads within a process but is not a distributed authority and settles estimates, not broker-reported fills/fees/collateral. **Multiple funded writers sharing a broker account are unsupported without a single account owner or transactional distributed ledger and broker reconciliation.**

## Verification and remaining release gates

The source-level, deterministic cases now exercise:

- an older successful/failed provider request completing after a newer circuit transition;
- one half-open probe admitted at a time, rejecting NaN/Infinity retry timing;
- an old venue probe racing a newly failed order or an operator circuit reset;
- six simultaneous equal market reads consuming one provider call, with frame isolation;
- bounded in-flight request waits, immediate overload denial, cache eviction and oversized-row pre-copy rejection;
- stronger quality-mode selection despite a weaker fast-mode cache, and registration/recovery with an in-flight old source;
- optional intelligence failure and restoration, refusal to bypass authorization denials or corrupt observations.

**Local targeted result:** 61 passing tests in the working container with targeted source mirrors and Python 3.13. Those tests do not establish success for all files at the GitHub head, the declared Python 3.11/3.12 runtime, live provider contracts, frontend, Rust, Windows, process-crash recovery, or network partitions.

Before merging this branch: restore GitHub Actions runner allocation, run pinned Black 24.10.0 and regenerate the module-accountability manifest, then require every supported CI job to pass at one stable commit. Before **funded live use**, additionally prove single-writer capital authority, broker-actual settlements and fees, safe gross-neutral multi-leg flatten, fencing across deployment hosts, and controlled venue fault-injection. A passing mock-driven regression suite is not sufficient for those obligations.
