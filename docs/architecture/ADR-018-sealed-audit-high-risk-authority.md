# ADR-018: Cryptographically Sealed Audit as a High-Risk Approval Prerequisite

**Status:** Accepted  
**Date:** 2026-07-26

## Decision

Require high- and critical-risk proposal approvals to present an independent approval credential and operate over a valid HMAC-sealed audit ledger with a signed head anchor. Preserve explicit chain-only status for deployments without a sealing key and deny high-risk approval there.

## Rationale

A plain hash chain detects some edits only when a trusted head is retained elsewhere. It cannot reliably detect truncation or authenticate entries after host compromise. High-risk authority requires stronger evidence than a mutable local file and the gateway API key alone.

## Consequences

- `AUDIT_HMAC_KEY` must contain at least 32 bytes and be protected separately from durable state.
- Missing/wrong keys and invalid/unsealed ledgers fail closed.
- Legacy hash-chain prefixes can be retained and anchored, with status reported as `sealed_with_legacy_prefix`.
- HMAC sealing is tamper evidence, not public non-repudiation or protection against total host/key compromise.
