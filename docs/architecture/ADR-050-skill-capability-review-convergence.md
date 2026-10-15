# ADR-050 — Skill intake, review integrity and capability preview convergence

**Date:** 2026-10-15. **Target:** draft TradingGYM PR #2.  
**Status:** independently tested source changes committed; no skill activation,
remote execution, grants, financial authority, or production release.

## Donor-to-owner mapping

The Skillbox donor implements canonical skill resource identities, digest and
size validation, safe import exclusions, and publication manifests.
The TypeSafe Skill Router donor uses choice/fitness questions and explicit
abstention, with a documented 255-choice limit for its remote scorer.

TradingGYM already owned `trading_gym/skills/intake.py`,
`quarantine.py` and `static_assurance.py`, so convergence strengthens
those modules rather than creating a duplicate skill/runtime controller.
Its existing hash-chained governance grants and tool registry retain their
respective responsibilities. Nothing imported grants authority.

## Reproduced defects and changes

- **Package intake:** the previous file reader accepted symlinks and
  unbounded package bytes. Intake now rejects final-component symlinks,
  opens files without following them on POSIX, reads at most 16 MiB, and
  requires a regular file. It returns size and SHA-256 plus
  *heuristic* risk-marker names, never matched credential text.
- **Hash and capability assurance:** previously any 64-character string
  qualified as a digest, and malformed capability types could be handled
  as character collections or raise exceptions. Static assurance now
  checks canonical lowercase SHA-256, bounded named capability arrays,
  duplicates, forbidden execution/payment/network/broker roots, risk
  markers, and non-activated quarantine posture. Reviewable is not
  executable.
- **Quarantine:** reviewer/preparer names previously admitted blank and
  case-alias identities, and return values/shared nested metadata could
  mutate the internal catalogue. Admission now checks distinct
  normalized principals, exact package hash, non-executable capability
  metadata, unique skill IDs, and bounded repository size; snapshots are
  deep copies. The repository remains in-process and non-durable.
- **Workflow DAG:** duplicate IDs previously overwrote one another, bad
  retry/timeout types passed loosely or raised inconsistent errors, and
  a 1200-node cycle exhausted Python recursion. The static assessor now
  bounds size/dependencies, validates finite durations and integer retry
  budgets, and uses iterative topological sorting with explicit blockers.

## Non-authoritative capability selection

The new `trading_gym.skills.capability_selection` is a pure, deterministic
human-review *preview*. Given an operator-supplied catalogue and an
independently supplied expected digest, it validates:

- explicit operator consent and expected owner;
- exact catalogue content digest and canonical package SHA-256;
- separate reviewer/preparer identities and unexpired review timestamps;
- quarantined, never-activated state with no risk markers;
- strict read-only capability allowlist and exact scope satisfaction;
- unknown extra metadata rejected without echoing its values.

It prefers the candidate with the least unnecessary read-only capability
breadth, breaking ties by stable skill ID. Report hashes and digest equality
**do not verify signatures, establish reviewer authenticity, or approve
execution**; all records remain caller-supplied. A stale, unknown or
untrusted candidate is excluded or produces no hint.

A new bounded offline interface is available:

```bash
python scripts/preview_skill_capabilities.py \
  --manifest reviewed-catalogue.json \
  --catalogue-sha256 <independently-pinned-lowercase-sha256> \
  --owner research_team \
  --capability research.read \
  --as-of-unix <review-timestamp> \
  --consent
```

Input JSON is size-capped, rejects duplicate keys/nonfinite literals and
symlink files, and reports sanitized error categories without showing
manifest secrets. It never reads skill code or invokes any model, tool,
MCP server, filesystem writer or broker.

### Why there is no automatic hierarchical classifier

This preview performs **no model call**, so the TypeSafe native
255-choice limit is not applicable. Hierarchical inference would add
complexity and ranking uncertainty without measured benefit.
Actual model-based skill selection, if desired later, requires separate
latency/cost/false-positive experiments and external data-disclosure
approval.

## TDD and verification boundary

RED reproduced six failure families against the pre-change source:
forged hashes/capability types, identity aliases and mutable records,
symlink/oversized imports, duplicate step IDs and negative retries,
NaN/boolean timing, and unbounded recursive workflow analysis.
GREEN: **17/17** focused in-process skill/capability tests.
The separately executed offline CLI suite passed **6/6** tests,
for **23 local passing cases**. All generated source files
compiled under the available Python 3.13 interpreter. Their GitHub
bytes were matched to locally tested source SHA-256 fingerprints before
updating module accountability.

**Not certified:** all existing repository tests, whole-app Python
3.11/3.12 matrix, frontend, Rust, native scorer behavior, release contracts,
human review identities, persistent catalogue security, and live execution.

## Second-order consolidation / remaining tasks

1. **Durable reviewed catalogue:** a hash-chained or signed, scope-specific
   snapshot with explicit provenance and versioned expiry; do not treat an
   operator-submitted digest as a signature.
2. **Role-scoped research UX:** render sanitized read-only preview results
   only through existing authenticated grants; do not introduce a second
   credential store, MCP executor or remote code loader.
3. **Capability lifecycle:** explicit review/expiry/revocation with
   independently authenticated principals, replayable review receipts and
   unambiguous denial for stale metadata. In-memory quarantine is not enough
   for multi-process deployment.
4. **Provider routing boundary:** model/tool recommendations remain separate
   from capital authority, venue settlement, risk checks and emergency
   flattening.
5. **Acceptance:** exact-head `pytest`, `black --check`, `ruff`,
   `scripts/audit_module_accountability.py --check`, package fixtures,
   UI tests and runner-backed CI are required before release.

The PR stays **draft and funded-live NO-GO**.
