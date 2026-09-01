# ADR-033: Self-Improvement Revision Surface

**Status:** Accepted
**Date:** 2026-09-01

## Decision

The self-improvement loop may rewrite the agent only through a single gate module (`trading_gym/agent/revision.py`) — the revision surface is the constitution; everything else is machinery that must pass through it:

1. **Five tiers**, each with thresholds and authority: **P**arametric (hyperparameters, thresholds — auto within grant bounds), **S**tructural (DSL strategy topology — auto when replicated on sealed holdouts), **M**eta (fusion weights, playbooks, prompts — auto with ≥5% lift, replicated), **B**ehavioral (planner policy — *never* auto; becomes a governance proposal), **X** code/artifacts (*never* self-revisable; the agent may only nominate candidates into the human absorption pipeline).
2. **Fixed gate chain**, evaluated in order and each failure named: **sealed holdout** (a candidate naming a holdout id it was not seeded with — a leakage attempt — fails; S/M candidates must replicate lift on every seeded holdout), **regression floor** (candidate ≥ champion × 0.98 on the shared suite), **diversity floor** (ensemble pairwise correlation must stay ≤0.70 — a self-improving agent's natural endpoint is monoculture that fits the past perfectly, and this makes it structurally unreachable), **canary** (planted known-bad candidates must fail; a canary that *passes* means the evaluation harness itself is broken, so the loop freezes and the decision is journaled as such).
3. **Additivity of decisions.** Every applied/rejected decision — with revision id, kind, target, named reason — is hash-chained into `TRADING_GYM_HOME/agent/revision/revisions.jsonl` (the grants/autonomy journal pattern), producing an auditable autobiography; tampering fails the open.
4. **Rate cap.** Applied revisions per rolling 24h window are capped (default 10, replayed from the journal on open); a runaway loop starves instead of oscillating.

## Rationale

A self-improvement loop is the most dangerous object in finance: it is a PBO machine pointed at its own evaluator. Three failure modes are structural, not procedural: overfitting the evaluator (sealed holdouts + replication on ≥2 windows), self-deception (canary violation tests — the only way the loop can lie is an evaluation bug, so the lie detector gets a lie detector that freezes the loop), and monoculture collapse (the diversity floor makes "one strategy that fits the past perfectly" unreachable regardless of headline performance). The five-tier surface exists because "the agent may rewrite itself" is not a permission — it is a family of permissions with different blast radii: a threshold tweak and an execution-path code change must never cost the same thing to authorize. X-tier nomination-not-apply keeps human absorption (and its ADR discipline) as the only path into the codebase itself.

## Consequences

- **Positive.** The loop (roadmap Phase 5–8) can be built incrementally — Optuna HPO, genetic DSL search, bandits — because every candidate funnels through one submit() whose guarantees are testable independently of the machinery producing candidates. The journal gives quarterly human audits a complete, tamper-evident record.
- **Negative.** Conservative gates reject marginal improvements (0.98 floor, 0.70 diversity ceiling, 24h caps) — deliberate: a frozen improvement costs less than a runaway loop. Evidence (suite/holdout scores) is caller-computed at v1; wiring it to the playground runner's manifests (ADR-031) is the first integration task, so scores become replayable artifacts rather than assertions.
- **Risks.** Objective drift — optimizing the metric instead of the mission (mitigation: mission-level KPIs inside the gate thresholds, quarterly human review of the journal); gate-gaming via candidate construction (mitigation: the canary system; plant adversarial candidates in scheduled drills).
- **Composition.** `authority()["self_revision"]` from the autonomy ladder (ADR-032) gates which tiers may auto-apply (`none` below L2, `parametric` from L2); B-tier outputs feed the governance proposal path; X-tier outputs feed the Donor Arsenal absorption pipeline (ADR-029).
