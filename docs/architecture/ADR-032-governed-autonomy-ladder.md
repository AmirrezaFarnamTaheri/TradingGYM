# ADR-032: Governed Autonomy Ladder

**Status:** Accepted
**Date:** 2026-09-01

## Decision

Agent authority over execution is expressed as a four-rung ladder (`trading_gym/agent/autonomy_ladder.py`) with structural guarantees, not operator discretion:

1. **Rungs.** `L0_SHADOW` (decisions journaled, nothing executes) → `L1_PAPER` (paper execution through the governed path) → `L2_TINY_LIVE` (live orders under tight ADR-028 grant constraints) → `L3_SCALED` (sustained operation, red-team findings closed). The rung mapping to authority is machine-readable: `AutonomyGovernor.authority()` returns exactly what the current level permits (paper orders? live orders? self-revision class?).
2. **No skipping.** Promotion is only ever one rung; a shadow-level agent presenting live-authority evidence (a critical proposal id) is denied — evidence for a rung you are not on is itself a violation signal.
3. **Evidence-gated promotion.** L0→L1 requires a full ≥30-day shadow window with ≥200 journaled decisions and zero constraint breaches; L1→L2 additionally requires DSR-positive fusion lift (≥0.05) and an approved critical proposal; L2→L3 additionally requires red-team findings closed and live-vs-backtest slippage within tolerance. Every gate denies with a named reason.
4. **Immediate demotion.** Any constraint breach drops the agent to shadow, journaled with reason and actor; after a demotion the shadow window *restarts* (a demotion timestamp gates the next promotion) — a violation costs the full window, not a keystroke.
5. **Tamper-evident journal.** Every transition is hash-chained into `TRADING_GYM_HOME/autonomy/autonomy.jsonl` (canonical JSON + SHA-256 + anchor head, the grants-ledger pattern); an edited or truncated journal fails its open.

## Rationale

The autonomy ladder is the roadmap's answer to "autonomy is earned, governed, and revocable." Operator discretion over "can the agent trade live" is exactly the failure mode the review's governance analysis warns about — authority drift without an audit trail. Making the ladder a state machine with hard evidence gates means the question "why is the agent at L2?" has a forensic answer: the journal entry that promoted it, the evidence bundle inside it, and the caller that asserted the evidence. Evidence is *asserted by the caller* at v1 (shadow-window accounting, DSR reports, red-team registry feed the fields) — the governor verifies shape and thresholds while the journal makes every claim attributable. Wiring evidence sources to live data (accounting ledger replay, monitoring SLOs) is Phase 5 work; the gate structure does not change when they land.

## Consequences

- **Positive.** L2 live autonomy no longer needs a human per order — the ADR-028 wildcard grant *is* the standing four-eyes decision, bounded and revocable in one command; demotion-on-breach is automatic (the risk gate calls `demote`), not a meeting. The `authority()` contract gives the planner and API a single fail-closed source for "what may this agent do".
- **Negative.** A 30-day shadow window per promotion (and per demotion) is deliberately slow — autonomy is purchased with clean operation time; evidence fields are caller-asserted until the Phase 5 wiring lands, so the journal's value depends on callers feeding it truth.
- **Risks.** Promotion-gaming (asserting evidence to climb rungs) — mitigated by attribution of every claim in the journal and, later, by cross-checking asserted evidence against the accounting/monitoring records it claims to summarize.
- **Composition.** The risk gate demotes on breach; `authority()` feeds the planner's self-revision tier gating (ADR-033: `self_revision` is `none` below L2, `parametric` from L2); live execution still requires the coordinator, wildcard grants, and emergency-kill path — the ladder gates nothing by itself.
