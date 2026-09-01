# ADR-029: Octopus Arm Architecture

**Status:** Accepted
**Date:** 2026-09-01

## Decision

The AXIOM-1 agent is organised as *one brain, many arms*: independent decision sources (pre-defined algorithms, DL models, news/research, rule-of-thumb playbooks, statistics, meta-lab) that the planner invokes in cost order and the fusion engine combines — with the cage (risk gates, breakers, governance) structurally outside the arm registry.

1. **Arm contract.** An arm wraps a decision source — usually an existing `IntelligenceBackend` (`trading_gym/agent/arms/base.py::BackendArm`) — with planner metadata: `tier` (`T1_RULES < T2_STATS < T3_MODEL < T4_LLM`), declared `CostModel` (latency p50/p99, credits per call), concrete `required_scopes` (ADR-028 grammar), and `staleness_tolerance`. Arms remain advisory: `propose()` never touches the venue.
2. **Scope pre-flight at registration.** Registration validates that every declared scope is concrete (no wildcards — so pre-flight is a plain ledger `check`) and grantable (no reserved namespaces). Fail-fast at wiring time, not mid-cycle.
3. **Blackboard seam.** Arms record `ArmProposal`s (signal + tier + cost) on a per-cycle `Blackboard`; the fusion engine and planner read from the blackboard only. Arms never call arms; arms never call execution. This keeps arms hot-swappable and independently testable.
4. **Tier cascade (progressive compute).** The planner invokes arms in tier order: T1 rules/playbooks every bar; T2 statistics on schedule; T3 model inference when cheap arms disagree or a regime gate demands it; T4 LLM on scheduled digests or cross-arm disagreement that blocks a decision. Every invocation reserves budget (existing `budgets/ledger.py`) and records a cost receipt.
5. **The cage is not an arm.** Guardians (circuit breakers, position gates, panic, audit, governance) cannot be benched, budget-starved, or deregistered — they are not in the arm registry at all. The octopus has a skull.

## Rationale

Three directives converge here: (1) the user's wildcard-authorisation model needs a bounded set of *scope-bearing actors* to authorize — arms are that unit; (2) "best performance, lowest cost" demands that expensive inference is the exception, not the default — the tier cascade makes cheap deterministic arms the always-on floor (the deterministic rules backend already proved this pattern in ADR-023); (3) the roadmap's anti-theater rule requires every new abstraction to ship with a working evaluator and a consumer test — `BackendArm` reuses the already-tested `IntelligenceBackend` implementations rather than defining a parallel decision type, and `TestArmAuthzComposition` proves the arm registry and the ADR-028 grant ledger compose (arms authorize against real grants; missing scopes bench the arm).

## Consequences

- **Positive.** New decision sources integrate by wrapping, with no trade-loop changes (the ADR-023 property holds one level up). Cost accounting gets a natural unit: the arm invocation. Budget breaches bench arms instead of corrupting decisions. Fusion (roadmap Phase 4) gets a clean input stream.
- **Negative.** One indirection between a backend and its use (one adapter class). Arm count needs a registry cap discipline (roadmap risk: arm sprawl) — enforced socially through the anti-stub rule, not yet mechanically.
- **Risks.** Stale arms making stale calls (mitigated: `staleness_tolerance` + planner pre-flight); cost model drift from reality (mitigated: cost receipts feed actuals back in Phase 5 attribution).
- **Composition.** The planner (Phase 5) consumes `ArmRegistry.in_tier_order()`; the fusion engine consumes `Blackboard.proposals()`; the authz `require_scope` dependency (API layer) pre-flights `arm.required_scopes` against the grant ledger before invocation.
