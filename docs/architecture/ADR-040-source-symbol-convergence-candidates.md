# ADR-040 — Symbol-level donor convergence candidates

**Status:** automated source-index evidence, not a semantic or implementation
equivalence claim (2026-10-15). **Applies to:** the 22 ZIP archives enumerated
in ADR-039. **Authority:** source review only; no executable donor import.

## Audit method

Starting from the immutable SHA-256 archive inventory (8,247 files and 1,574
folder records), a separate source-symbol crosswalk filtered generic names,
known vendored/generated paths, and tests; it grouped named exported functions,
Rust `fn` declarations, classes, structs and interfaces by normalized identifier.
This yielded **64 cross-donor shared-name groups** and **4,421
single-archive name groups** under its heuristic filter. Static naming
similarity is *only a discovery lead*: unrelated code can share a name, and
synonymous or superseding implementations can have different names. Eight
archives yielded no usable cross-donor symbol matches under these filters;
that is not evidence that they contain no original capabilities.

### Highest-value cross-project comparison leads

| Name/contract family | Donor examples | Actual integration decision |
| --- | --- | --- |
| `scoreAnswer` / `scoreQuestion` | JevLoop, OpenJev, pi-jev, JevRev | The typed `choice`/`noul` adapter validates response shape, decision label and risk-off veto; do not combine incompatible scoring formulas or assume calibrated correctness |
| `resolveProvider` | 9Router, JevLoop, jev-gateway | Preserve TradingGYM's governed provider inventory and immutable authority; add only the disabled preset/route-preview advisory planes |
| `estimateTokens` / `countTokens` | Conduit, JevLoop, graph tools | Admission estimates need model-specific tokenizer evidence; token-count heuristics alone cannot authorize payload truncation or billing |
| `contextWindow` / `LLMProvider` | codegraph-rust, graphify-rs, jev-codex-router | Introduce exact context/token capacity only with verified provider metadata; model-route preview already rejects unknown pricing and too-small windows |
| `dependency` / `snapshot` | Conduit, codegraph-rust, graphify-rs | File-hash impact tool remains a lower-bound static source graph, separate from operational service-dependency and deployment authority |
| `acquireLock` / `clearCache` | JevRev, codegraph-rust, Conduit | Shared locks, ledger integrity, stale cache and atomic file update must be separately tested for each persistent owner; not reusable merely because names match |
| `generateReport` | Conduit, codegraph-rust, graphify-rs | Use deterministic content-bound receipts and independent holdout verification; reports cannot approve live trading |
| `cacheKey` / `parseArgs` | 9Router, Conduit, JevRev | Different runtimes and data classification; adopt only bounded, non-secret, versioned and collision-safe contracts |

## Further second-order architecture

1. Source-level symbol overlap should be joined to **call-graph and type-contract
   evidence**, not accepted as supersession; semantic diffing requires aligned
   fixtures and behavior-first tests.
2. Developer tooling can rank similar modules for review with stable
   provenance, but code generation and import remain gated and isolated.
3. Model routing, memory/context compilation, and native typed inference
   require independent **cost, quality, privacy and authority controls**.
4. Policy and execution decisions must not depend on unverified source
   similarity or probabilistic agent assessment.

The reproducible index and per-entry hash/line references are available in
the companion conversation artifacts `all_donor_symbol_crosswalk.json` and
`all_donor_symbol_crosswalk_overview.md`; those artifacts are **not**
bundled as executable dependencies in this PR.

## Unresolved acceptance

No static symbol register proves review of every source method. A full
cross-language semantic graph, full donor test/dependency suite, actual
native-model calibration and capital authority certification remain
outstanding. The repository remains draft and funded-live **NO-GO**.
