# Whole-project simplification and architecture clarity

## The prompt

```text
Apply these skillsets throughout TradingGYM:
- /simplify
- /ai-devkit:simplify-implementation
- /ai-dev-kit:refactor-clean
- /codebase-visualizer:codebase-visualizer

This is an implementation request, not a request to generate another prompt. Review the whole project, not just the current diff, and deliver verified improvements.

OPERATING CONSTRAINTS
Load the available skills and combine their useful methods into one coherent pass. Work directly without subagents, including where a skill normally delegates. Do not install missing plugins or execute unknown slash commands; report unavailable capabilities and apply their described principles directly.

Work autonomously with sensible defaults. Do not ask onboarding questions, create repeated planning artifacts, or stop at a proposal when implementation is authorized. Ask only for genuinely unresolved authority, destructive-action, spending, or correctness decisions. Never invent approval or bypass mandatory host permissions.

Preserve all uncommitted work and newer upstream behavior. Verify branch state and relevant upstream integration notes; do not blindly replay recovery stashes or overwrite implementations already fixed upstream. Do not commit, push, deploy, publish, spend money, or place live orders without explicit authorization.

ESTABLISH EVIDENCE
Map entry points, responsibilities, dependencies, public interfaces, and implemented user journeys. Reuse existing documentation only after checking its accuracy. Run baseline checks before changes and classify failures as existing defects, dependency gaps, platform restrictions, or introduced regressions.

Measure real complexity: function/file size, nesting, duplicate logic, dependency direction, repeated I/O, allocations, startup work, or latency where relevant. File size alone is not proof of poor design. Review all major areas without forcing a change in every directory.

APPLY FOUR REVIEW LENSES
1. Reuse: find existing helpers and platform features that fit cleanly. Remove reimplementations without forcing unrelated operations into a shared abstraction.
2. Simplification: remove unnecessary state, dead branches, speculative layers, copy-paste, and confusing names. Prefer readable control flow over clever brevity. Prove suspected dead code has no runtime, public API, plugin, CLI, or dynamic-dispatch consumers before deleting it.
3. Efficiency: eliminate demonstrated redundant computation, I/O, excessive retention, and unbounded work. Measure performance claims; do not add caching, concurrency, or services speculatively.
4. Altitude: fix complexity at its source rather than layering special cases onto callers. Preserve cohesive boundaries and explicit dependencies; do not merely move a god function into another file.

For every candidate, first ask whether it needs to exist. Prefer deletion, standard-library features, and established project utilities. Keep small duplication when consolidation would hide meaningful domain differences. Do not add languages, dependencies, or parallel frameworks for cosmetic consistency.

IMPLEMENT SAFELY
Use characterization tests before refactors and failing regression tests before defect fixes. Make one coherent change at a time; verify before proceeding. Keep behavior-preserving refactors separate from intentional behavior changes, and explain the latter explicitly.

Preserve public schemas, import compatibility, route precedence, error semantics, persistence formats, and supported configuration. Keep financial amounts, rounding, order states, and reconciliation behavior equivalent unless a separately reproduced defect requires correction.

Never weaken authentication, principal-bound approval, deterministic risk gates, idempotency, unknown-outcome holds, audit integrity, fencing, emergency controls, or credential isolation. Do not unify safety-critical paths whose authority, locking, recovery, or accounting contracts differ. LLM advice and memory cannot expand execution authority.

VISUALIZE THE ACTUAL ARCHITECTURE
Use the visualizer to produce or update a self-contained local HTML guide with component responsibilities, dependency direction, and representative user/data flows. Link real source files and distinguish observed behavior from proposed architecture and unverified integrations. Prefer a small number of meaningful groups; do not imply every group is a deployed service.

Include keyboard navigation, readable labels, responsive layout, theme support, and reduced-motion behavior. Verify interactions and layout in an available browser. If browser verification is blocked, state that limitation. Avoid fabricated coverage, performance, support, or production-readiness claims.

VALIDATE AND DELIVER
Run affected tests during each increment, then broader regression checks, lint, typing, builds, and applicable security checks. Verify changed-logic coverage where feasible; never equate coverage with correctness. Regenerate derived contracts and manifests through existing tools. Keep test artifacts out of tracked operational data.

Finish with implemented changes and source links, justified before/after measurements, exact verification results including failures and skips, the visual guide link, deliberately deferred candidates, and remaining risks. State the actual scope reviewed and validated. Do not claim whole-project perfection or behavior equivalence beyond the evidence.
```

## Implementation notes

The four cleanup lenses provide a consistent review rubric; existence checks and domain-boundary constraints prevent abstraction churn. Incremental verification makes broad repository coverage compatible with small, reversible changes. The prompt is model-independent and asks for evidence, not private reasoning transcripts.

## Usage guidelines

Paste into a coding session at the repository root. Optionally append a time budget or areas of greatest concern. Expected outputs are working cleanup changes, an accurate local architecture guide, and an explicit verification/deferment record. Unavailable tools and dependencies must be reported rather than silently replaced with claims of success.

## Optimization tips

Prioritize repeated maintenance burden and complete user journeys over line-count targets. Use targeted tests for rapid feedback and broader suites at integration checkpoints. Avoid repeated inventories, cosmetic rewrites, over-generalized helpers, and shipping visualization features unrelated to explaining the codebase.
