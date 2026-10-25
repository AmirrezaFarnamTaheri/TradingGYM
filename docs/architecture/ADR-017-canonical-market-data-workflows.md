> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-017: Canonical Instruments and Provenance Before Backtest Work

**Status:** Accepted  
**Date:** 2026-07-26

## Decision

Require canonical instrument resolution, bounded timezone-aware data windows, validated OHLCV, content/schema hashes, provider provenance, and dataset lifecycle state before creating research backtest jobs. Cross-plane workflows create experiment records only and grant no execution authority.

## Rationale

Peer implementations disagreed on symbols, providers, caching, and backtest entry points. Treating raw symbols or downloaded frames as authoritative permits alias collisions, silent data changes, incomplete coverage, and irreproducible results.

## Consequences

- Instrument aliases are unique and revisioned.
- Data identity and quality are inspectable and quarantineable.
- Workload and leakage controls are planned before worker execution.
- Existing execution and broker adapters remain outside this workflow.
