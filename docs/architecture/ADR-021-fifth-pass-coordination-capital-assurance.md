# ADR-021: Fifth-pass coordination, capital and assurance boundaries

## Status

Accepted for Trading Gym 9.0.0.

## Context

Trading Gym 8.0.0 had strong local authority, accounting, execution, recovery and governance contracts. The remaining gaps were primarily compositional: stale workers could not be evaluated through a shared fencing model; event evidence lacked a general replay oracle; capital eligibility and treasury timing were separated from portfolio marks; policy and schema changes lacked consistent impact review; and operational, compliance and assurance evidence could exist without a mandatory shared gate.

## Decision

Introduce additive, target-native evidence planes with these constraints:

1. **One authoritative side-effect path remains.** New modules evaluate, reserve evidence, or create review artefacts. They do not execute the operation under review.
2. **Fencing is monotonic.** Lease generations, not owner labels, determine whether a worker is current.
3. **Replay is strict.** Gaps, duplicates, mixed streams, chronology errors and hash mismatches are blockers rather than partial successes.
4. **Capital is availability-aware.** Haircuts, encumbrance, unsettled value, concentration, liquidity and dated cash obligations are distinct from nominal market value.
5. **Security classifications cannot relax through compatibility mode.** A confidentiality downgrade is incompatible in backward, forward and full review.
6. **Failover cannot sacrifice data freshness for nominal health.** A candidate behind the latest timely sequence is not promotable.
7. **Statistical evidence is predeclared and multiplicity-aware.** Sequential looks and multiple hypotheses cannot manufacture significance.
8. **Operational evidence must be executable in principle but non-executing in the product.** Runbooks, change plans and failover reports prove readiness without applying changes.
9. **Qualification binds full evidence.** Counts and blocker names are insufficient; the normalized payload is hash-bound and every configured class must be present, fresh and authority-negative.

## Rejected alternatives

- Reusing donor-specific actor, portfolio, or deployment runtimes would create duplicate authorities and incompatible persistence models.
- Treating healthy replicas as automatically failover-ready ignores sequence loss and stale state.
- Allowing forward-compatible confidentiality relaxation would convert a schema mode into a policy bypass.
- Automatic remediation, scaling, deployment, failover, policy activation or model promotion from evidence reports would collapse separation of duties.
- Shared mutable in-memory registries would not survive restart and could not support deterministic audit evidence.

## Consequences

The platform gains coherent coordination, replay, capital, policy, schema, statistical, operational, compliance and forensic review contracts. It also gains more evidence requirements: qualification is intentionally harder to pass, and deployment-specific calibration remains mandatory. Operators must still use the existing governed approval and execution paths for all side effects.
