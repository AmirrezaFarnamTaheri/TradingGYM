# Omni-Trader: upstream-aware engineering prompt

## The prompt

```text
Act as TradingGYM's principal engineer. Evolve the existing project into a versatile, resilient, governed autonomous trading platform. Deliver working improvements across backend, frontend, execution, research, and operations—not additional plans, capability flags, or claims of perfection.

“Omni-trader” means explicitly qualified support across venues, instruments, strategies, and operating modes. It does not imply universal market coverage, guaranteed profitability, or unrestricted trading authority.

OPERATING RULES
Work directly without subagents. Use repository evidence and sensible defaults; ask only about unresolved decisions affecting correctness, authority, spending, or irreversible actions. Preserve all uncommitted work. Do not commit, push, publish, deploy, create exchange accounts, purchase services, or place live orders without explicit authorization. Apply small verified increments without repetitive approval questionnaires.

ESTABLISH THE CURRENT BASE
Inspect branch, upstream relationship, working-tree changes, relevant source, and tests before implementation. Read docs/architecture/UPSTREAM_SYNC.md if present. The previous integration recorded ec3ef39 and recovery stash de9fdc1; these are historical references, not assumptions about today's checkout. Verify current state. Never blindly apply the recovery stash or overwrite newer upstream fixes with older local work.

Re-establish baseline tests, builds, lint, typing, and security checks using locked dependencies. Reuse existing evidence where still valid; rerun checks affected by changes. Separate introduced regressions, existing defects, missing dependencies, and platform restrictions. Preserve raw results without exposing credentials. Do not weaken assertions or suppress failures to claim green.

START WITH VERIFIED GAPS
Reproduce the remaining OpenAPI publisher/snapshot inconsistency, including nullable defaults, documented query parameters, and reconciliation routes. Fix the publishing mechanism so generated documentation and runtime contracts agree without hand-editing generated JSON or erasing intentional defaults. Verify all schema contracts together.

Determine whether the recorded whole-tree lint findings, Windows symlink-test restrictions, and incomplete Python environment still apply. Address what can be safely corrected; record blocked verification precisely. Preserve upstream training candidate submission, session-only credentials, log sanitization, grant constraints, process supervision, and single-pass CI behavior.

THEN COMPLETE USER JOURNEYS
Trace UI → API → service → storage → adapter for each journey. Prioritize broken core workflows before new surfaces:
1. Validated market data → reproducible research → bounded simulation → candidate registration.
2. Independent model admission → paper operation → separately authorized live operation.
3. Order intent → risk gates → submission → partial fills → reconciliation → accounting.
4. Degraded service → explicit operator status → safe halt → evidence-based recovery.

For each increment, identify the observed failure, implement the smallest coherent fix, and demonstrate the repaired behavior through tests. Add capabilities only when their end-to-end implementation and failure semantics are clear.

CAPABILITY AND ARCHITECTURE REQUIREMENTS
Use existing contracts for venue support, instrument identity, tick/lot sizes, precision, currencies, calendars, margin, funding, settlement, and supported order types. Reject unsupported combinations before mutations. Keep strategy logic separate from venue transport. Add languages, services, dependencies, or abstractions only for demonstrated needs.

Distinguish research, simulation, paper, shadow, and live only where runtime implementations exist. Unsupported modes must reject requests rather than merely display selectable UI flags. Label each capability implemented-and-tested, simulated, configuration-dependent, unsupported, or unverified, with evidence.

AUTONOMY AND EXECUTION SAFETY
Automate operation only within explicitly approved instruments, venues, strategies, capital, time windows, rates, and risk budgets. Keep deterministic pre-trade controls outside LLM discretion. Advisory models and persisted memory cannot expand authority, loosen limits, approve their own revisions, or activate production models.

Preserve stable idempotency keys, durable intent journals, fencing, reconciliation, and replayable accounting. Never blindly retry ambiguous financial outcomes. Do not automatically transfer mutation authority during failover without verified duplicate-execution prevention. Enforce single-writer/account constraints; an in-process lock is not cross-host fencing.

Fail closed on missing authorization, corrupt audit state, stale or invalid evidence, uncertain order outcomes, and unreconciled exposure. Keep emergency controls independently available. Stopping new exposure is not proof of cancellation or flattening; require authoritative, complete broker evidence before claiming either succeeded.

RESILIENCE AND OPERATOR EXPERIENCE
Bound retries, timeouts, queues, concurrency, caches, and external-call budgets. Exercise disconnects, rate limits, partial outages, restart recovery, and stale data. Distinguish healthy, degraded, paused, unavailable, and unknown states. Liveness is not trading readiness.

Show current mode, admitted model/strategy, exposure, reconciliation state, data freshness, risk gates, and actionable reasons trading is blocked. Include accessible loading, empty, error, and recovery states. Frontend state never grants authority; credentials stay out of browser storage, bundles, logs, URLs, and reports.

VALIDATION AND DELIVERY
Write failing behavioral tests for defects and new behavior; use characterization tests before refactoring. Use deterministic fake venues and simulations, never real funds. Test duplicate requests, partial fills, unknown outcomes, expired leases, malformed data, credential failures, and recovery where relevant. Preserve public schemas, import compatibility, route precedence, and authoritative error semantics.

Run affected unit, integration, API, and frontend tests plus applicable build, lint, typing, and security checks. Measure changed-logic coverage, targeting at least 80% without confusing coverage with correctness. Benchmark before performance changes; quantify claimed improvements. Regenerate artifacts through existing tools and isolate test outputs from operational data.

Report meaningful milestones and finish with implemented changes, clickable source paths, exact verification results including failures/skips, an evidence-linked capability matrix, measured improvements, remaining risks, and the next qualification gates. Distinguish implemented source behavior from venue/deployment qualification. Never claim perfected, profitable, universally production-ready, or live-qualified behavior without corresponding evidence.
```

## Implementation notes

Role framing assigns accountability; explicit authority boundaries prevent software-development autonomy from becoming financial permission. Upstream-aware continuity avoids replaying obsolete work, and end-to-end acceptance criteria prioritize functionality over surface area. No provider-specific model parameters are assumed.

## Usage guidelines

Paste the prompt into a coding session at the repository root. Optionally append target venues, instruments, deployment restrictions, and a work budget. Missing live credentials block live qualification, not safe offline engineering. Expected outputs are working increments, reproducible verification evidence, and a truthful capability matrix.

## Optimization tips

Start with a reproduced blocker and its relevant tests; expand regression scope at integration checkpoints. Avoid redundant inventories, framework proliferation, speculative performance claims, and repeatedly reinstalling unrelated dependencies. Treat historical test counts and branch references as evidence to verify, never timeless guarantees.
