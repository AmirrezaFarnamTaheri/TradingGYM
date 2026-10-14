# ADR-028: Wildcard Capability Grants for Delegated Agent Authority

**Status:** Accepted
**Date:** 2026-09-01

## Decision

Governed agents (AXIOM-1 octopus arms and their planner) receive authority through *delegated wildcard grants* rather than per-action human approval. A grant binds a principal to grantable scope patterns plus numeric constraints, is born through the normal four-eyes governance proposal path, and is spent via a local, fast, fail-closed evaluation:

1. **Scope grammar.** Scopes are dotted, lowercase, segment-exact: `domain.resource.action` with segments `[a-z0-9_-]{1,64}`. `*` matches exactly one segment; `**` must be terminal and matches zero or more remaining segments. Matching is segment-wise over pre-tokenised pattern lists — no user input is ever compiled to a regex, so matching is linear and immune to ReDoS.
2. **Reserved space.** First segments `emergency|governance|wallet|authz` and the fully-qualified scope `audit.seal` are never grantable, not even via leading `*` (rejected at grant time) or a bare `**` catch-all. The parser enforces this structurally: a pattern whose first segment is not a literal non-reserved name is not grantable.
3. **Grant object.** `{grant_id, principal, scopes[], constraints{}, state, issued_at, expires_at, issued_by}`. Constraints (`max_notional_quote`, `max_orders_per_hour`, `max_drawdown_pct`, …) are numeric ceilings checked against caller-observed values; the ceiling store never reads ledgers itself — composition stays in the API/coordinator layer where the existing auth chain (API key + actor principal) is verified first.
4. **Evaluation is deny-by-default and fail-closed.** Unknown state, expired TTL, revoked grant, missing constraint ceiling, malformed ceilings/values, or ungrantable patterns produce denials or explicit errors — never silent allows. Constraint breaches *latch* the grant to `revoked-breach` in the journal; re-issue requires a fresh governance proposal.
5. **Durable, verifiable journal.** Every mutation (issued/revoked/breach) is appended to `TRADING_GYM_HOME/authz/grants.jsonl` under the platform's crash-safe append helper, hash-chained exactly like the governance ledger (canonical JSON, SHA-256 over `prev_hash`+payload), with an anchor file pinning the chain head. Tampering (edit, reorder, truncation) fails ledger open. State is rebuilt by replay; malformed records fail the open rather than yielding half-trusted state.
6. **Authority ceiling.** The API layer validates at issuance that the proposing principal's own roles cover the grant's scope-union (a paper-only principal cannot issue a `trade.live.*` wildcard). The scope grammar and ledger enforce well-formedness; ceiling checking remains a governance concern and is implemented at the API boundary (not bypassable, not duplicated here).

## Rationale

The four-eyes path is the platform's strength — and its bottleneck for a standing agent that acts every bar. Requiring a human click per order would push operators toward over-broad standing credentials; delegating *bounded, revocable, auditable* authority restores the operator's time while keeping the guarantee that matters: no agent action ever escapes the governed authority path. The design reuses the platform's proven primitives (canonical-JSON hash chaining from `governance/ledger.py`, crash-safe JSONL appends from `common/persistence.py`, deny-by-default posture from the API auth chain) rather than inventing a second credential system. Wildcards are parsed into token lists, not regexes, closing the classic "over-broad `trade.*`" hazard with a testable grammar (property tests prove `**` = unbounded `*` runs, first-segment anchoring, and most-specific-wins). Constraint breaches latch rather than just deny, so a runaway agent loses its leash automatically instead of repeatedly knocking on a closed door.

## Consequences

- **Positive.** Live autonomy (roadmap Phase 8 L2) no longer requires a human per order — the grant is the standing four-eyes decision, revocable in one command and latching on breach. Every past authorization decision is forensically reconstructable from the journal. The grammar is cheap to test exhaustively (54 unit+property tests currently green).
- **Negative.** Grant issuance remains a governance event, so operators must learn one new object. Evaluation adds a per-request in-memory check (~µs), plus a JSONL append only on issuance/revocation/breach.
- **Risks.** Over-broad patterns (mitigated: reserved-first-segment rejection, no leading `*`, property tests, golden deny tests in `tests/authz/`); breach-latch storms (mitigated: latch is idempotent for already-latched grants; journal rate is bounded by issuance discipline).
- **Composition.** `/api/v1/authz/*` routers and `require_scope` dependencies consume this module; arms declare `required_scopes` and are pre-flighted against it (ADR-029). A future `sandbox-intersection` rule (playground strips live scopes) extends this module without changing it.

## New-issuance protection (2026-10-14)

The grammar remains backward-compatible with already-signed journal entries,
including historically broad patterns. **New** grants are stricter: wildcard
patterns in live, production, payment/economic, broker/venue or comparable
capital-sensitive namespaces are rejected at the ledger's `issue` boundary.
`trade.paper.**` and read/research wildcards remain available; individual
concrete live/economic scopes still require ordinary issuer governance.

Historical broad grants are **not silently rewritten or deleted**, since doing
so would invalidate signed provenance and make revocation impossible.
`GET /authz/health` now exposes
`legacy_high_risk_wildcard_grants`; operators must review and explicitly
revoke each such grant before funded deployment. This additive restriction
does not provide transactional settlement authority or substitute for
independent live-order approval.

## Deny-overrides-allow and complete observations (2026-10-14)

Every active grant matching a concrete scope participates in a decision.
One matching grant's failed ceiling overrides an otherwise-permissive
overlapping grant, irrespective of issuance order; unrelated scopes do not
veto. All constraints defined on a grant require current observations on
every evaluation, and non-finite/malformed numeric values fail closed.
`GrantLedger.require` uses the exact `check` verdict instead of having a
different, first-allowing traversal. A grant is never active before its
`issued_at`, and a previously returned grant handle cannot authorize
an action after the owning ledger has revoked it. This protects in-process
handles; separate workers must still reload the durable journal and require
runtime governance at the execution boundary.

For historical records containing invalid numeric ceilings, direct evaluation
raises `ConstraintViolation` and ledger-level checks return an explicit
`invalid_constraints` denial. Signed journal bytes remain unchanged.

## Journal anchor integrity under complete truncation (2026-10-14)

On verified open, a journal with zero entries **must not** have a residual
anchor, and a missing journal with an anchor is treated as a truncation.
Malformed, oversized, nonobject or symlinked anchors, unreadable journals
and nonobject event records raise the typed `GrantLedgerIntegrityError`
instead of unhandled `JSONDecodeError` or `AttributeError`.
The anchor contains exactly `seq` (integer, not boolean) and `head`
(string), verified against the complete journal tail. This defends against
truncation while the anchor survives; a privileged actor who removes both
the journal and anchor still needs an **external trusted checkpoint** to
be detected. Those stronger, externally anchored guarantees are not claimed.

## Constrained issuance must be serializable and unambiguous (2026-10-14)

`GrantLedger.issue` now rejects malformed constraint names, duplicate
ceiling entries, nonnumeric/boolean/negative/non-finite ceiling values, and
more than 32 distinct ceilings before appending to the signed journal.
Boolean TTLs no longer pass the Python integer check. This prevents
invalid or ambiguous new signed records, while preserving the ability
to parse existing, historically malformed records for independent
forensic review and explicit revocation.

## Atomic lifecycle validation across ledger handles (2026-10-14)

The per-journal chain lock already serialized hash-chain writes. It now
also checks the post-reload lifecycle transition before appending:
`issued` IDs must be new, while `revoked` and `breach` require
an active current grant. A stale second revocation raises without
changing disk state; a stale breach is a no-op. Semantic replay rejects
duplicate issued IDs and repeated state transitions even if a historical
journal's hash sequence was rewritten consistently. This closes a
same-filesystem cross-handle race but does not qualify an unrelated
machine or external authoritative grant store.
