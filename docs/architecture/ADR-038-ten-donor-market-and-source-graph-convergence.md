# ADR-038 — Ten-donor source convergence, market integrity and source-impact plane

**Date:** 2026-10-15. **Status:** partially implemented, independently unqualified.
**Target:** draft PR #2, \`audit/2026-10-11-execution-ci-hardening\`.
**Safety:** read-only research, operator observability and development tools only;
no capital authorization, order submission, merge, release or deployment.

## Scope and evidence

Ten supplied ZIP files contain 4,210 archive-member entries across 1,124
observed nested directories; duplicate Git/source copies, tests, media and
binaries are deliberately not equated with independent product features.
An all-member SHA-256 inventory was generated separately (with 3,929 unique
content hashes and 251 groups containing identical payloads). The ZIP
inventory is *exhaustive as file metadata*; this ADR is a semantic analysis
of the major product/service/algorithm clusters, not a proof that every
function was behaviorally reviewed, executable, or correctly licensed.

Compared against the **existing** TradingGYM PR branch, not against a
made-up clean rewrite. Important preexisting seams:

- \`trading_gym/institutional_os/microstructure.py\`: simulated book fill,
  queue priority and implementation-shortfall assessment.
- \`trading_gym/market/orderbook.py\` and
  \`trading_gym/adapters/orderbook_integrity.py\`: market snapshot admission.
- \`trading_gym/operations/dependency_impact.py\`: human-declared
  service-level graph, capacity and rollback blast radius.
- \`trading_gym/intelligence/system1_context.py\`: compact provenance-checked
  advisory market packets; \`llm_backend.py\` and \`llm_key_pool.py\`: provider
  transport and cross-process credential failover.
- \`apps/api/intelligence.py\`: authenticated read-only backend status.
  \`frontend/src/components/IntelligencePanel.jsx\` existed but was **not
  mounted in operator navigation**.

## First-order source-to-target decisions

| Donor | Grounded components | Convergence decision |
| --- | --- | --- |
| \`Conduiit(3).zip\` | \`crates/source\`, \`crates/change\`, runtime evidence, BLAKE3-anchored edits, subprocess instrumentation, embedded ripgrep/RTK/zap | Reuse hash-bound impact and review **ideas**, not a second execution engine; Python/JS/Rust offline source graph now added. Independent native crate, license and compiler qualification is required before any binary port. |
| \`jev-main(4).zip\` | Go \`internal/run\`, \`internal/scan\`, \`internal/evidence\`, calibration, snippet narrowing, source snapshot locks | Keep point-in-time, digest-bound evidence. Do not confuse code snippet retrieval with valid market provenance or put search answers in a live decision. Duplicate embedded tree/binaries are not additional capability. |
| \`9router-master(2).zip\` | account fallback, OAuth/token refresh, format translation, SSE, combo routes, quota and analytics | Reuse audited provider semantics selectively. PR already has ordered System-1 providers, key rotation and per-provider request admission. A raw port of silent account switching, 403 retries or billing-cap bypass would violate TradingGYM authority. |
| \`jev-gateway-main.zip\` | \`src/decide.ts\`, \`questions.ts\`, \`events.ts\`, \`usage.ts\`, multi-client translation and routing | Useful for **developer tool-choice hints**, not autonomous live-trading choices. Operator status now exposes request budgets; prompt/credential content stays off status responses. Gateway fail-open is inappropriate for grant or risk denial. |
| \`pi-jev-main.zip\` | \`src/gate.ts\`, \`src/output.ts\`, tool risk/impact assessments, output judge, thresholds | Use as a proposed *non-authoritative* risk-review suggestion. Current explicit capability grants, signed review and trading gates remain authoritative; uncalibrated classifier probabilities cannot grant approval. |
| \`graphify-rs-main.zip\` | Rust extract/detect/analyze/export/cache/watch/serve modules, multi-language call graph and graph algorithms | Extract file-level import graph and reverse impact locally; retain later symbolic graph, incremental reindex, pagerank/cluster and visual exports as benchmark-dependent optional developer features. |
| \`codegraph-rust-main.zip\` | parser/vector/core/graph/mcp layers, SurrealDB, hybrid search, LSP context, agent workflows | Avoid dependency-heavy runtime imports. Adopt offline digested source impact and a bounded report; full LSP and vector indexing require recall/error, storage and security benchmarks. |
| \`graphify-dotnet-main.zip\` | C# CLI/SDK/MCP, graph export and rendering (JSON, SVG, Mermaid/Neo4j/Obsidian/Ladybug) | Preserve portability as export opportunities, not a .NET dependency inside Python live workers. The current source graph is explicit about only syntactic lower-bound coverage. |
| \`jev-trader-main.zip\` | \`src/book.ts\`, \`src/trades.ts\`, \`src/trader.ts\`, block-stream book and execution dashboard | **Adapted** read-only, bounded spread/depth-imbalance/microprice diagnostics plus finite-data validation; declined direct on-chain order placement, speculative fills or mock strategy promotion. |
| \`jev-ultrafast-main.zip\` | \`jev_ultrafast/agent.py\`, \`model.py\`, speculative browser work, latency measurements | Parallel speculative action paths suit offline research at most. No prefetched browser command or unverified fast-model branch may spend capital or override risk gates. |

Source integrations were **original, targeted implementations**, not verbatim
copies of upstream donor runtimes. Licenses for every archive and embedded
third-party directory must still be validated before any future direct copy.

## Implemented changes and API contracts

### Snapshot and market-evidence integrity

\`OrderBookSnapshot.validate()\` now rejects bool/non-integer sequences,
non-finite or nonpositive levels, duplicate prices within a side, incomplete
and crossed books. The stateful \`OrderBookIntegrityGate\` invalidates its
sequence and requires a new full snapshot whenever a sequential payload is
malformed. Its historically named \`apply_delta\` **expects a complete new
book at the next sequence**, not a sparse level mutation; sparse vendor deltas
must be merged and verified in a future adapter before calling it.

\`normalized_orderbook_features(book, depth=5)\` provides four bounded,
finite, non-authoritative diagnostics:

- \`spread_bps\`: best ask minus best bid relative to the midpoint;
- \`depth_imbalance\`: signed top-N visible depth share, between -1 and 1;
- \`top_bid_fraction\`: top-N bid depth / total depth, between 0 and 1;
- \`microprice_offset_bps\`: opposite-side weighted top-of-book microprice
  deviation from midpoint, in basis points.

All fields are computed from the same validated book; order levels can be
unsorted but duplicates and crossed states fail closed. This does **not**
add live context to a model automatically: a separate trusted feed time,
closed-bar provenance, book sequence and as-of policy are required to do so.

### Source graph and impact plane

\`scripts/source_impact.py\` is a standalone, non-mutating tool:

\`\`\`bash
python scripts/source_impact.py --root . \
  --changed trading_gym/market/orderbook.py
\`\`\`

It indexes Python AST imports, literal relative JS/TS imports and a bounded
subset of Rust \`mod\`/\`crate::\` references. It hashes files, resolves reverse
transitive paths, and reports deterministic \`report_sha256\` plus explicit
\`coverage: static_lower_bound\` and \`non_authoritative: true\`. It denies
unknown/outside changed files, symlink escapes, malformed Python, and
unbounded input. It is **not** a complete symbol call graph: dynamic import,
JS aliases, macro expansion, package re-export and virtual dispatch may
produce missing edges. It does not substitute for compiler checks or
release qualification.

This complements rather than replaces the existing **service** dependency
assurance, which evaluates criticality, rollback and capacity of declared
operational dependencies. A future source-to-service mapping must be explicit,
versioned and reviewable, never an inferred authorization.

### Operator provider dashboard

The existing \`IntelligencePanel\` now has a navigation destination within
the four existing operator workspaces, without removing other tabs. It shows
the configured fast-model roster, experience-memory opt-in and a bounded
provider request-admission view. \`GET /intelligence/status\` adds per-provider
read-only \`request_budget\` state when a strict request cap exists.
Budget exhaustion or corrupted ledger makes the advisory provider
unavailable for selection hints; response bodies contain no key, secret,
internal path or raw exception message. Compute diagnostics are now
included in the normal status response. Key rotation does not create
new request budget.

## Second-order architecture (integration seams, not deployed features)

1. **Evidence/Source Graph plane:** source snapshot hashing and transitive
   impact, then explicit human-owned source-to-operational-service
   associations, then review evidence. Any future graph engine is a
   replaceable developer adapter, not a second capital authority.
2. **Point-in-time Context Compiler:** combine verified closed candles,
   independently timestamped book records, named normalized indicators,
   and settled memories. Require source+event-time+observed-time+bar-close
   linkage and leakage tests before exposing order-book features to System-1.
3. **Model Fleet Control plane:** one provider identity catalogue,
   credential references, request admission, provider-wide and org-wide
   budget scopes, sanctioned retry taxonomy, and sanitized telemetry.
   9Router-style account switching and Jev-style tool-choice routing are
   consumers of this policy, never an override.
4. **Operator Observability plane:** stable health/usage/budget reports and
   user-visible descriptions of degraded, unprobed, denied and ready.
   No raw prompt, approval keys, account balances or broker credentials.
5. **Capital Authority plane:** single authoritative account and venue
   settlement reconciler, gross-flat emergency readback, distributed
   fencing, verified fees/fills, and fault-injection qualification.
   No donor supplied a verified substitute for these gates.

## Verification and current disposition

Observed local source-mirror RED failures: malformed book values were
accepted and source-graph behaviors were unimplemented. After repair, focused
tests passed: **12 market-book + 6 source-graph scenarios**.
The source-graph tool indexed extracted supported sources across all ten
donor archives without a syntax or directory traversal failure.

New repo test cases cover book integrity, source graph/paths, request budgets,
operator navigation and sanitized API responses. **Do not call those
exact-head checks green** until CI runs against this PR head.

Mandatory remaining gates: Python 3.11/3.12 \`pytest\`, Ruff and pinned
Black, module-accountability \`--check\`, frontend Vitest and build, compiled
Rust tests/clippy/fmt, security/load/venue integration, source release
evidence and independent review. Hosted Actions runners previously
failed before allocating any job steps; confirm current-head status.

Unsettled external choices: provider/project-wide versus organization-wide
quota sharing, allowed model presets, telemetry retention, symbol graph
toolchain, event-time policy for cross-venue books, model calibration
thresholds, and verified broker emergency semantics.

**NO-GO:** funded live, unverified automation, branch merge or production
release.