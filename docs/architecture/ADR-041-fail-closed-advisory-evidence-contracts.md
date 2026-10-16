# ADR-041 — Safe fallback taxonomy and point-in-time System-1 evidence

**Date:** 2026-10-15  
**Target:** draft TradingGYM PR #2, `audit/2026-10-11-execution-ci-hardening`  
**Disposition:** source changes committed; whole-repository and funded-live certification **not obtained**.

## Motivation: source-level failures confirmed

A follow-up review of the 22-donor convergence found four operational safety
gaps in TradingGYM's implementation, not features that require importing
another uncontrolled agent runtime:

1. The decision policy treated contradictory LONG/SHORT leverage as an
   ordinary model failure. A fallback could then emit a new risk-on trade,
   turning invalid evidence into changed financial authority.
2. An inconsistent native System-One probability distribution could be
   classified as a recoverable backend outage and trigger a trading
   fallback rather than deny the malformed response.
3. The model-route *preview* accepted a candidate with unknown remaining
   request-admission capacity as eligible.
4. A source bar with a close timestamp up to three seconds **after**
   the current time was labeled `verified_closed_bar`.

An additional source-accountability audit found that the generated manifest
tracked **344** Python modules while the Git tree contained **346**. It omitted
both `llm_key_pool.py` and `system1_context.py`, preventing source-fingerprint
coverage for key rotation and fresh market context.

## Implemented authority contracts

### Invalid model advice is terminal

`IntelligencePolicyAdapter._signal_to_action` rejects non-finite or
non-numeric output, boolean confidence/leverage, invalid action-kind values,
out-of-range confidence and contradictory direction/leverage with
`PermissionError("invalid_intelligence_decision")`. These failures do not
continue to a risk-on rules or RL fallback. Unexpected structural exceptions
from a backend are likewise terminal
(`intelligence_backend_invalid_failure`); recoverable
`RuntimeError`/`OSError` and the existing `BackendUnavailable` taxonomy
still permit operator-configured fallback. Original network and provider
fault tolerance is preserved; the change concerns *invalid financial advice*.

Native typed `systemone_protocol` malformed distributions/answers become
`PermissionError("systemone_invalid_model_output")`, while the explicitly
recognized `systemone_low_confidence_escalate` signal may use a separately
configured fallback. A verified explicit HOLD remains zero exposure.

These checks are **not a replacement** for broker, reservation, leverage,
pre-trade, venue settlement or emergency-flatten authority.

### Live decision-time provenance is not anticipatory

The bounded System-1 packet rejects a bar whose claimed close timestamp is
still in the future. The earlier three-second tolerance was inconsistent
with the `verified_closed_bar` label: a bar cannot already be closed at
`now < bar_open + interval`.

The `source_sha256` field remains a fingerprint supplied by the upstream
feature adapter, not a cryptographic proof that an exchange independently
attested to the contents. Venue observation, clock synchronization, account
readback, hash canonicalization and as-of replay require separate audit.

### Model-routing hints reject unknown admission

The offline preview now excludes `remaining_requests=null` with reason
`request_budget_unknown`, alongside already-denied exhausted, unprobed,
unqualified, over-cost or unconsented remote candidates. This does **not**
change the runtime model registry or automatically activate providers.

### Source accountability restored

The Git tree contains **346** application Python modules under
`trading_gym/` and `axiomforge/`. The SHA-256 manifest now includes
both previously omitted core files and the changed modules' refreshed
LF-normalized hashes in generator order. Because the full repository is not
available for local execution, exact-head
`python scripts/audit_module_accountability.py --check` remains a CI
acceptance gate. An exhaustive every-file fingerprint recheck was attempted
but could not complete within connector call limits.

## Recorded RED → GREEN test evidence

Isolated reproduction of the **same decision/fallback seam**:

- RED: 4 cases incorrectly accepted contradictory/non-finite decisions as
  degradable; GREEN: 8/8 tests including ordinary recoverable outages and
  permission denials.
- RED: native invalid probability returned `BackendUnavailable`; GREEN:
  29/29 typed protocol and native-backend isolated tests after separating
  schema corruption from weak-confidence escalation.
- RED: 1 router case admitted unknown budget capacity; GREEN: 10/10 route
  preview isolated tests.
- RED: 4 future-close timestamps received "fresh" status; GREEN: 5/5
  boundary tests after enforcing nonnegative close age.

The final isolated mirror ran **66/66** broader tests and the standalone
authority/freshness fixture ran **13/13**, with syntax compilation successful.
These are honest **isolated-source-mirror** results, not a green production
checkout. The exact modified source snippets were pushed with corresponding
tests on PR #2; repository integration, all workflows and release qualification
remain unverified.

## Second-order convergence: next admissible architecture planes

- **Release-Evidence Plane:** run one exact-source `--check` pipeline for
  module hashes, OpenAPI, tests, frontend/Rust build and banked artifacts,
  anchored to a single Git SHA. An incomplete runner does not produce a
  passing run receipt.
- **Market-As-Of Plane:** upgrade a format-checked source digest into a
  witnessed snapshot identity with event/observation timestamps, exchange
  provenance and canonical candle/feature content, then benchmark delayed
  close and clock-skew behavior.
- **Model Qualification Plane:** pair model IDs with pinned artifact digests,
  leak-free holdout evaluation, abstention calibration and independent
  operator-approved eligibility, never auto-promote from probability alone.
- **Execution Authority Plane:** continue separate broker readback, fills,
  fee/funding reconciliation, distributed fencing and gross-flat emergency
  verification before funded-live activation.

## Release status

PR #2 remains draft and unmerged. On the observed exact head, GitHub has
not reported a successful Actions suite. The historical CI job failures
executed zero steps and do **not** establish source compilation results.
No production or funded-live approval is granted.
