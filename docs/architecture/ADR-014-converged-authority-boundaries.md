> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-014: Converged capabilities remain non-authoritative

- Status: Accepted
- Date: 2026-07-26

## Context

Peer projects contain valuable strategy, research, provider, agent, automation, and product mechanisms. Directly transplanting their runtimes would create duplicate sources of truth and could let generated code, prompts, triggers, or UI state bypass Trading Gym risk and execution controls.

## Decision

Adopt the useful semantics through bounded target-native planes. Declarative strategy specifications replace arbitrary generated code. Triggers and agents create proposals only. A closed approval lifecycle, revision compare-and-swap, separate high-risk approval key, and one-time consumption sit before the existing execution boundary. UI state is never authoritative.

## Consequences

- Donor value can be used without donor-specific runtimes or unsafe authority.
- Promotion requires explicit adapters and evidence; it is intentionally not automatic.
- Operators gain auditable review and replay resistance.
- Distributed deployment requires a transactional persistence implementation of the same contracts.
