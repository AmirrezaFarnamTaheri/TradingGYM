> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-030: Fusion Engine and Deterministic Tier-Cascade Planner

**Status:** Accepted
**Date:** 2026-09-01

## Decision

AXIOM-1's cognition core is two pure, deterministic components:

1. **Fusion engine** (`trading_gym/agent/fusion.py`). One blackboard of arm proposals in, one fused advisory decision out. Directional proposals vote with contribution `confidence × weight`, weights keyed by `(arm, regime)` (`FusionWeights`; entries are managed, absent arms vote unmanaged at 1.0; explicit 0.0 benches an arm for that regime). HOLD proposals are neutral.
   **The anti-laundering rule:** fusion never converts a split vote into a position. It degrades to flat (`DisagreementEscalated`, `target_leverage=0.0`) when the directional share `|net|/total` ≤ `disagreement_margin` (default 0.15), when total conviction mass < `quorum_confidence` (default 0.4), when the roster is empty, or when every directional arm is zero-weighted. Magnitude is the contribution-weighted mean of the winning side only, clamped to `±max_leverage`.
2. **Deterministic tier-cascade planner** (`trading_gym/agent/planner.py`). The L0–L1 brain: a state machine over `(context, regime, grants, budget)` with no clock or randomness — identical inputs replay to identical schedules and decisions, which is what makes the audit trail meaningful. Arms are considered in tier order (T1→T4); each arm must pass pre-flight (available → scopes granted against the ADR-028 ledger → budget), any failure benches the arm with a reason rather than aborting the cycle; after each tier the fusion engine votes and the cascade stops at the first decisive vote. All-escalate returns the final escalation — flat plus review. The planner never touches the venue.

## Rationale

The two failure modes a fusion layer must not have are (a) laundering a split roster into a confident position and (b) letting expensive inference run when cheap determinism already decided. The margin/quorum rules make (a) structurally impossible — disagreement produces flat plus a reviewable escalation, never "average and hope". The tier cascade answers (b): T1 rules decide most bars in microseconds; T3/T4 run only when cheap arms disagree, and the `test_decisive_t1_stops_cascade` test proves the expensive arm is never invoked in that case. Determinism is non-negotiable below L2 autonomy: an agent whose planner cannot be replayed cannot be audited. Both components are pure functions of their inputs — no I/O, no venue access, no side effects — so property tests and the existing hypothesis infrastructure can fuzz them without sandboxing.

## Consequences

- **Positive.** Fusion lift, disagreement rate, and per-arm contribution are now measurable (telemetry rides on `PlannerResult`); benched arms are a first-class observable; the escalation path gives Phase 5's human-review queue its natural input. The cascade gives "lowest cost for best performance" a mechanical form.
- **Negative.** Two new tunables (`disagreement_margin`, `quorum_confidence`) need calibration before live use — defaults are conservative; fusion confidence is currently the directional share, a coarse proxy for calibration (Phase 5 replaces it with Brier-calibrated confidence).
- **Risks.** Fusion weight overfitting — the roadmap's own CSCV/PBO gate applies to any learned weights (v2 meta-labeling work); regime tag quality gates everything (Phase 2's regime arm and drift baselines are the mitigation).
- **Composition.** `PlannerResult.fused` feeds the decision journal (existing events envelope); scope pre-flight consumes `GrantLedger.check` (ADR-028); budget pre-flight consumes the existing `budgets/ledger.py` reservations via the `budget_check` seam (wired in the API layer, keeping this module I/O-free).
