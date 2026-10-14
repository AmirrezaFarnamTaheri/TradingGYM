# TradingGYM donor convergence and second-order architecture review (2026-10-15)

**Target:** Existing draft PR #2 (`audit/2026-10-11-execution-ci-hardening`); base `main`. **Disposition:** source integration only, not a production release or funded-live approval.

## Source coverage and limitations

These are the actual supplied archives, not claimed upstream checkouts:

| Archive | Archive members | Uncompressed size | Directories / practical scope |
| --- | ---: | ---: | --- |
| `Conduiit(3).zip` | 1,267 | 13.88 MB | Unified Rust CLI, source catalog, structural graph, change review, source policy, tool telemetry, tests |
| `jev-main(4).zip` | 348 | 42.92 MB | Go source search, calibrated semantic evidence, read narrowing, verification snapshots, audit/usage, tests; includes duplicate nested source, binaries and Git metadata |
| `9router-master(2).zip` | 1,630 | 13.32 MB | Next.js/JS provider gateway, account failover, combo routes, auth, token refresh, translation, SSE, quotas/usage, dashboard and tests |

Inventory was performed from ZIP metadata and extracted application sources. It does **not** prove that every source line was semantically reviewed, that any donor passes its own suite, or that another project's runtime can be safely embedded into this Python trading application. The source archives are not automatically executed.

## First-order adoption / adaptation

| Capability | Donor evidence | TradingGYM existing seam | Decision |
| --- | --- | --- | --- |
| API-key rotation | 9Router `open-sse/services/accountFallback.js` | `intelligence/llm_key_pool.py` | Existing process-shared rotation is stronger for this use case; **do not replace** terminal 403/429 behavior with broad account fallback |
| Provider request quota | 9Router usage and quota service family | `agent/providers.py`, `intelligence/llm_backend.py` | **Implemented:** optional `max_requests_per_hour` per provider; shared file-locked counter, each HTTP attempt charged before sending |
| Semantic source search | Jev `internal/run/find.go`, `internal/scan` | developer tooling / offline source analysis | **Defer:** keep out of funded decision path; evaluate in developer tools with calibrated evidence and external dependency disclosure |
| Snapshot-bound evidence | Jev `internal/evidence/snapshot.go` | module-accountability/release evidence | **Adopt design discipline, not code:** bind checks to exact source and command identity; CI must be executed on this PR head |
| Structural navigation / impact | Conduit `crates/source/graph`, `crates/change/change-review` | refactoring and architecture auditing | **Defer executable import:** Rust toolchain/dependency and graph correctness unverified in donor; use as optional separate developer utility |
| Token output compression | Conduit runtime; 9Router RTK | system1 bounded market context | **Do not copy into decision context:** lossily compressing market provenance/risk facts is unsafe |
| Multi-model combos | 9Router `open-sse/services/combo.js` | System-1 roster and strict policy adapter | **Adapt selectively:** retain explicit ordered failover; no silent paid-provider switching, authorization bypass or trading-signal-voting side effects |
| Provider-specific OAuth / translations | 9Router `open-sse/executors`, `translator`, `services/tokenRefresh` | OpenAI-compatible advisory transport | **Do not import wholesale:** different language/runtime, token policy and attack surface; add only contractual adapters after tests |
| Context selection & evidence windows | Jev `internal/run/locate.go`, `internal/evidence` | `system1_context.py`, optional memory | **Keep point-in-time, closed-bar and historical-hindsight safeguards**; semantic code-retrieval results must not become trading evidence |

## Implemented request admission contract

- `ProviderConfig.max_requests_per_hour` is an **optional** strict positive integer with maximum 100,000. `None` preserves the existing provider behavior.
- The configured cap applies to each upstream HTTP attempt, including retries across keys. Shared state is scoped to configured provider ID + base URL, **not** key, model, or API worker.
- A single JSON state is published by atomic replace under `filelock`; counters reset at the next UTC hour. Exhaustion, malformed state, reversed clocks, lock unavailability, and unsafe symlink paths deny advisory provider admission. Lower caps take precedence among concurrently configured workers during a window.
- Each attempt is charged **before** network I/O; failed/ambiguous calls are **not refunded**. The budget is deliberately conservative, advisory-only, and does not authorize trades.
- No API key values, prompts, responses, transaction secrets or order payloads are put into the counter file.
- The budget is **not** a monetary/tokens quota, cannot verify provider billing or free-tier entitlements, cannot coordinate independent data homes, and is **not tamper-proof** against a privileged operator with direct write access. Configure actual provider-side project budget limits as well.

Example provider field:

```json
{"max_requests_per_hour": 120}
```

## Second-order convergence opportunities — design only, not shipped

1. **Research evidence plane:** unify source-aware code navigation (Conduit) with probabilistic semantic retrieval (Jev) as *offline engineering tools*, producing source-hash anchored provenance. Require independent tests before using generated candidate changes; never auto-apply them to a trading runtime.
2. **Advisory inference service plane:** a provider/model capability catalogue, deterministic routing policy, request/token/spend admission, outcome taxonomy, and full telemetry, separate from the capital authority plane. Existing System-1 selection becomes a consumer rather than duplicating credential and provider choices.
3. **Point-in-time decision-context compiler:** extend fixed market packet generation only with timestamped, provenanced evidence cards (market event time, observed time, generation hash and source), tested to reject future leakage and oversized essential evidence. Feature selection should be benchmarked versus current static packet.
4. **Risk and execution invariant plane:** consistent capital owner, live reservation fencing, verified fills/fees, venue reconciliation, order-state recovery and gross-neutral emergency tests **before** funded deployment. Donor routers do not solve these needs.
5. **Verification & release plane:** pinned Python/Rust/JS toolchains, source-accountability manifest, offline fixture integrity, immutable run receipts, historical/paper/chaos benchmarks and real venue certification. A missing GitHub runner is not a passing test.

## Verification, outstanding decisions and merge gates

An isolated checkout-independent Python budget test suite ran **7/7 passing**, including spawned-process contention. The checked-in repository test additionally exercises actual provider transport through a fake HTTP client; that exact-head test **has not run**. Existing GitHub Actions account/runners remain a reported blocker; confirm job allocation for the new PR-head SHA.

Required commands on a checkout of PR #2:

```bash
poetry install --with dev
poetry run pytest -q tests/intelligence/test_request_budget.py tests/intelligence/test_system1_rotation.py tests/intelligence/test_authority_isolation.py
poetry run python scripts/audit_module_accountability.py
poetry run black . --check
poetry run ruff check .
poetry run pytest -q
```

Also run repository frontend, compiled Rust, security, broker and integration checks. Do **not** merge, publish or enable funded trading until same-head CI, independent review, live broker settlement/recovery and production authority gates pass. Request quotas are opt-in; default cap, provider organization-wide scoping, token/USD budgets, and semantic context feature adoption require explicit product and risk policies.
