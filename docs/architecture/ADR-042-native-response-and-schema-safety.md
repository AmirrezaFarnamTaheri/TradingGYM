> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](../README.md). Historical commands, endpoints and verification counts below apply to that component only.

# ADR-042 — Signal invariants, typed health probes, and response admission

**Date:** 2026-10-15  
**Target:** TradingGYM draft PR #2 (`audit/2026-10-11-execution-ci-hardening`)  
**Authority:** advisory-only. No broker, credential, order, risk-grant, or deployment authority added.

## Confirmed integration problems

During the post-convergence audit of the typed Go System-One and
OpenJev adapter, three independent defects remained in existing PR code:

1. `DecisionSignal` accepted booleans as real-valued leverage/confidence
   and accepted raw string action labels. It emitted inconsistent
   `TypeError` instead of an explicit domain error on numeric strings.
   Some non-adapter consumers (notably fusion) receive signals directly.
2. Native provider health accepted any HTTP **405** from the typed route as
   evidence of reachability. Go System-One's actual handler responds to
   unsupported GET requests with `Allow: POST`. Generic proxy or WAF 405
   responses without this declaration do not establish route support.
3. Native transport treated malformed successful JSON, non-object JSON,
   and oversized successful HTTP bodies as recoverable
   `BackendUnavailable` conditions. Such responses could reach
   a configured risk-on fallback instead of becoming a terminal
   evidence-integrity denial.

A fourth contract mismatch was found in the manually edited OpenAPI file:
nullable `ProviderConfig` fields `api_key_env`, `base_url`,
`command` and `max_requests_per_hour` lacked the Pydantic-generated
`default: null` entries.

## Implemented contracts

### Strict domain validation

`trading_gym.agent.intelligence.types.DecisionSignal` now requires an actual
`ActionKind` enum, a finite real-valued leverage scalar and a finite
real-valued confidence in [0,1]. Booleans, numeric strings and non-finite
values raise the new `InvalidDecisionSignal(ValueError)`.

`IntelligencePolicyAdapter` explicitly catches the domain error and
converts it into `PermissionError("invalid_intelligence_decision")`,
preventing the configured fallback chain from manufacturing fresh
financial exposure. NumPy finite real scalars remain supported.

This is stricter *value typing*, not a model-quality guarantee. The
existing policy adapter still owns sign/direction consistency, neutral
HOLD and action-space bounds.

### Native System-One route probe

A **405** reports `reachable` only when the exact configured typed endpoint
also advertises `Allow: POST` (case-insensitive, comma-separated method
matching). An empty or GET-only Allow header is not sufficient.

A reachable route does not attest to a loaded checkpoint, model accuracy,
calibration, GPU availability, or authorization to send financial data.
Health checking remains non-mutating and does not invoke inference.

### Malformed response admission

Native `httpx_systemone_transport` treats a successful HTTP response that
is malformed JSON, not a JSON object, or above its byte ceiling as a
terminal `PermissionError`. Network/model outages keep the separate
`BackendUnavailable` path for explicitly configured fallback.
Unknown model identity and malformed typed answer distributions were
already treated as terminal failures in ADR-041.

The response-size check is currently performed on the received
`httpx.Response.content`, so it limits parsing/acceptance but not all
network buffering. A streaming byte-budget transport remains further
hardening work if untrusted high-volume endpoints are adopted.

### Published schema

The four nullable ProviderConfig property defaults were reconciled with
the locally reconstructed Pydantic 2 schema. The published contract parses
and describes 206 API paths. A new test compares nullable defaults against
the imported runtime `ProviderConfig.model_json_schema()`.

Full byte-for-byte `python scripts/export_openapi.py --check` from the
entire pinned FastAPI application remains **not executed**.

## Test and provenance evidence

A focused standalone Python 3.13.5 source mirror established RED and GREEN:

- Domain-value cases: **7 originally failing**, then 9 passing including
  valid NumPy reals.
- Native POST health probing: **2 originally failing**, then 4 passing.
- Malformed native HTTP envelopes: **3 originally failing**, then
  3 passing, including an oversized body.
- Combined focused regression: **16/16 passing**, syntax compilation clean.

These tests use source-mirrored seams and controlled HTTP fakes. They are
not evidence that the Python 3.11/3.12 repository test matrix, React,
Rust, or broker/venue integration passes.

`docs/contracts/module_accountability.json` was updated for the changed
Python modules. The Git tree and manifest were checked for exact path
coverage: **346 source modules / 346 entries**, none missing or duplicated.
A full all-file hash comparison still needs the repository generator on
an exact-head checkout.

## Next acceptance gates

1. Restore GitHub Actions runner allocation; run all required checks on
   one exact PR head, including tests, static analysis, OpenAPI and module
   accountability generator checks.
2. Exercise a real pinned Go System-One instance with valid/invalid typed
   inputs, wrong model ID, global HTTP 429, payload caps, and response
   shape. The donor's Go toolchain is newer than the available local 1.23.2.
3. Certify context freshness against exchange timestamps and independent
   source snapshots; avoid treating response shape as model calibration.
4. Retain separately governed broker settlement, order readback,
   reservation fencing, gross-flat emergency and funded-live approvals.

PR #2 remains draft, unmerged, and **funded-live NO-GO**.
