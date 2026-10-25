> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-015: Research is an evidence dossier, not an oracle

- Status: Accepted
- Date: 2026-07-26

## Decision

Research inputs are normalised into timestamped evidence items with source, quality, facts, and optional content hash. Dossiers deterministically group evidence and explicitly mark missing sections. Generated perspectives must cite evidence IDs. Reflection rejects unsupported and unknown citations and flags inferences from absent data.

## Consequences

Provider outages and incomplete coverage remain visible. The platform can compare research outputs reproducibly, while retaining the boundary that research is neither financial advice nor execution authority.
