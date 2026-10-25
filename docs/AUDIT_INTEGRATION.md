> Scope: retained research/compatibility documentation. It does not describe the current three-view cockpit or qualify an external service. For current setup and operation, see [the documentation index](README.md). Historical commands, endpoints and verification counts below apply to that component only.

# Audit integration — 23 October 2026

## Input identity and scope

This change integrates all 40 patches from the supplied TradingGYM review bundle
against base commit `3cdbc0a563a81a3a7d8e9c49b9ce7ef314e97e1b`.
The combined patch SHA-256 is
`f510d090cdb94f112686860e9f73d09ac94d37aad1cd7ffd7275dd79d1c5fdc1`.
The bundle's 40 patch hashes and 109 checksum entries were verified, the live
repository matched the declared base, and the strict application helper admitted
all 150 original file afterimages before the follow-up fixes below.

The source coverage register (historical artifact `docs/reviews/2026-10-23/source-coverage.json`, retired; recover from Git history) preserves
the supplied 234 outcomes, implementation locations, historical evidence and
remaining conditions. Its evidence statements describe the supplied candidate;
current integration checks are recorded separately below. Counts describe the
bounded software claims in that register. They do not certify a deployment,
strategy performance, real venue fills, or a funded account.

| Item | Scope | Implemented | Partial | Experiment | External |
| --- | --- | ---: | ---: | ---: | ---: |
| M01 | Session onboarding and authenticated entry | 4 | 1 | 0 | 0 |
| M02 | One read-only-aware GUI bootstrap | 4 | 1 | 0 | 0 |
| M03 | Validate intelligence configuration and fee reservations | 4 | 0 | 0 | 0 |
| M04 | Give paper accounts durable financial ownership and observe live state | 8 | 1 | 0 | 0 |
| M05 | Fit separate models on explicit chronological folds | 5 | 1 | 0 | 0 |
| M06 | Hash-bound frozen preprocessing in model artifacts | 5 | 0 | 0 | 1 |
| M07 | Share closed-bar availability and bounded feature history | 5 | 0 | 0 | 1 |
| M08 | Align baseline and policy decision clocks | 4 | 1 | 0 | 0 |
| M09 | Use elapsed market time and explicit live observation clocks | 4 | 1 | 0 | 0 |
| M10 | Bound episode horizons and separate account halt lifetime | 4 | 1 | 0 | 0 |
| M11 | Intersect final fill constraints and reprice projected depth | 7 | 0 | 0 | 0 |
| M12 | Preserve spread provenance and separate candle range | 5 | 0 | 0 | 0 |
| M13 | Report one continuous episode with explicit metric units | 5 | 0 | 0 | 1 |
| M14 | Strict decision admission and failure taxonomy | 6 | 0 | 0 | 0 |
| M15 | Observation-aware planning and evidence deduplication | 6 | 0 | 0 | 0 |
| M16 | Durable immutable decision attribution and event links | 6 | 0 | 0 | 0 |
| M17 | Bounded provider streaming retries and decision admission deadline | 5 | 1 | 0 | 0 |
| M18 | Use explicit fixed fusion and preserve execution units | 4 | 0 | 1 | 0 |
| M19 | Consistent Fisher kurtosis and probability gates | 4 | 0 | 0 | 1 |
| M20 | Revision-bound validation and honest CI diagnostics | 4 | 0 | 0 | 1 |
| M21 | Installed UI container and artifact delivery gate | 2 | 3 | 0 | 0 |
| M22 | Accessible navigation and real job response envelopes | 4 | 1 | 0 | 0 |
| M23 | Extract training service and preserve observed stop state | 5 | 0 | 0 | 0 |
| M24 | Bounded storage headroom and measured JSON baseline | 5 | 1 | 0 | 0 |
| M25 | Fail closed on unqualified application recovery | 3 | 2 | 0 | 0 |
| O01 | Bind full observation semantics and provenance to model ZIPs | 5 | 1 | 0 | 0 |
| O02 | Record temporal roles and reserve final evidence before HPO | 5 | 0 | 0 | 1 |
| O03 | Compare frozen observation representations without financial authority | 0 | 0 | 5 | 1 |
| O04 | Bridge deterministic execution into existing financial replay ledger | 5 | 1 | 0 | 0 |
| O05 | Forward context recorder and staged restriction experiment | 1 | 1 | 4 | 0 |
| O06 | Independent specialist replay and delayed outcome labels | 1 | 0 | 5 | 0 |
| O07 | Bounded residual training wrapper and matched comparisons | 1 | 0 | 4 | 1 |
| O08 | Explicit account-aware degradation admission | 6 | 1 | 0 | 0 |
| O09 | Ledger-derived settled paper memory and causal retrieval | 6 | 1 | 0 | 0 |
| O10 | Make turnover bands and rewards account consistent with cost experiments | 4 | 0 | 1 | 1 |
| O11 | Finite-sample conformal boundaries and delayed-label experiment | 1 | 0 | 4 | 1 |
| O12 | Measured stateless student ONNX deployment and boundary parity | 0 | 0 | 6 | 1 |
| O13 | Durable reference run bridge with leased completion | 7 | 0 | 0 | 0 |
| O14 | Read-only observation admission and coverage | 5 | 1 | 0 | 0 |
| O15 | Guided first run and truthful process outcomes | 6 | 1 | 0 | 0 |

Source totals: **171 implemented, 22 partial, 30 experimental, 11 external**.
All 40 parent patches are included. A parent patch can contain several kinds of
outcome; including its source does not close every qualification condition.

## Follow-up integration fixes

- Settle each replay action through the next valuation timestamp, including the
  last holding interval; use actual elapsed time for financing and evaluate
  interval risk against the next bar. Bind that timing contract into model
  observation identities, and align replay metrics and charts with valuations. Existing policies require retraining and a new
  bundle; see [valuation timing and model migration](VALUATION_TIMING.md).
- Give random baselines independent seeded generators so unrelated NumPy calls
  cannot change their replay sequence.
- Track canonical market scopes and consumed time intervals in evidence-role
  audits. Changing artifact hashes cannot refresh an already consumed final
  period, and consumption records must be chronological across procedures.
- Bind cancellation responses to the submitted run identifier, handle gateway-key
  rotation without confusing authorization denials with expired sessions, read
  risk settings from the API, and display unknown telemetry and unconfirmed
  shutdowns explicitly. Wait for the frontend before opening the launcher URL.
- Reject custom-backend HOLD signals with nonzero leverage before action
  construction, and preserve uncertainty as an exception without an admitted
  signal. Fallback requires explicit admission rather than a fabricated flatten.
- Add an independent real-dependency contract CI job, optional locked ONNX
  dependencies, explicit Node contract checks, and a required aggregate gate
  that fails on skipped, cancelled or failed prerequisites. The release script
  requires every advertised backend and frontend check.
- Repair integration fixtures to verify the changed contracts, retain exact
  builtin-type admission at validation boundaries, fix scoped type errors, and
  refresh generated schema/module inventories after source changes.

> **Post-publication qualification:** The results immediately below belong to
> the original frozen implementation tree, not automatically to later PR
> commits. Follow-up bug fixes, local regression evidence and blocked hosted
> runs are separately recorded in
> post-publication verification (historical artifact `docs/reviews/2026-10-23/post_publication_verification.md`, retired; recover from Git history).

## Current verification

The validated source is commit `865beaf83ae067923aa293c1fc8b17b2f0fb8400`, tree
`4afaffb779b0a5c25457faf031ba6ee48d3d602f`. The subsequent documentation commit changes
only this audit summary and the machine-readable validation record (historical artifact `docs/reviews/2026-10-23/validation.json`, retired; recover from Git history).
The complete Python suite and isolated contract suite retained the same source
SHA-256 throughout execution:
`5b16788cb0ea950565a6b318488367dfa1c385eb141ee2b25907cf00422ed32e`.

| Check | Recorded result | Scope |
| --- | --- | --- |
| Complete Python suite | **3,461 passed, 43 skipped, 0 failed/errors** | 3,504 collected; normal conftest, real-dependency guard and isolated state |
| Independent contract gate | **396 passed, 0 skipped/failed/errors** | 55 files under `--noconftest`; real PPO/HPO, ONNX, financial and API/worker contracts |
| Frontend component gate | **113 passed in 21 files** | Final frozen frontend; before/after hashes match |
| Native Node contracts | **3 passed across 2 files** | Job-response and wallet contract files |
| Frontend production build | **Passed** | Final source; build completed after the last frontend edit |
| Launcher checks | **7 passed** | Mocked service/browser lifecycle and configuration checks |
| Source quality | **Passed** | Ruff, Black 24.10.0 (849 files), Flake8 error selection and scoped MyPy (36 files) |
| Generated/release checks | **Passed** | Live OpenAPI, 389-module inventory, release manifest, workflow pins, recovered coverage and permissions |
| Dependency/configuration checks | **Passed** | Poetry lock consistency and CI/shell configuration checks |

The 43 Python skips comprise 42 checks needing unavailable compiled native
artifacts and one pre-existing explicitly skipped performance-scaling test. No
Python-runtime or ONNX dependency was missing from the executed gates. The
contract tests overlap the complete suite; their counts are separate execution
evidence, not additional unique test cases.

The complete suite took 306.745 seconds wall time and the independent contract
gate 131.01 seconds. The final frontend rerun covered 66 source/configuration/test/
asset files without changes. It supersedes the earlier component run whose last
trading-panel edit occurred during execution. Existing React `act` warnings and
dependency deprecation warnings remain non-failing.

These are local Python 3.12.14 results with installed real dependencies. Selected
core versions differ from the lock: SciPy 1.17.1 versus 1.16.3, PyTorch 2.2.2+cpu
versus 2.9.1, Stable Baselines3 2.2.1 versus 2.7.0, ONNX 1.17.0 versus 1.16.2 and
ONNX Runtime 1.21.1 versus 1.18.1. The JSON record includes the captured versions,
additional recorded differences, result counts and evidence digests. Hosted
checks for the published PR must be assessed separately.

## Qualification still required

- A fresh exact-lock Linux/Windows CI run, installed real-browser journey and
  built-container smoke test. Local dependency execution is recorded separately
  from the locked CI runtime.
- Real market, provider and venue evidence, including executable costs, actual
  fills and deployment latency. Synthetic/reference runs and optional model
  experiments do not supply that evidence.
- An all-writer consistent recovery snapshot and isolated financial
  reconciliation. The existing recovery contract remains fail-closed when its
  scope cannot establish those guarantees.
- Hard cancellation of arbitrary CPU/DNS work beyond the implemented bounded
  synchronous provider admission, and production retention/capacity qualification.
- Qualification of the full fresh-model-to-persistent-paper workflow and the
  optional experimental hooks with admitted application/account state.

The synthetic 96-bar reference journey is an executable product fixture with
persistent artifacts. Promotion and funded execution continue to require their
existing independent authority and evidence gates.
