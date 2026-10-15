# Hardening branch status — 2026-10-11

## Cross-worker terminal LLM credential/quota denials — 2026-10-15

Implemented typed, persisted fail-closed routing for the multi-key
System-1 provider fleet. Bad keys may fail over to a distinct valid key
within a bounded attempt budget, but exhausted HTTP 401 authentication
cannot turn into a risk-on rules/RL fallback. HTTP 402, and newly
recognized spend/credit/usage-cap 429 codes now retain a hard denial
across workers, even after the first request has completed. Invalid
provider request/model statuses 400/404/409/413/422 are terminal.
The existing temporary-rate-limit path and explicit per-key rate
opt-in remain intact; quota locks and ordinary cooldowns have distinct
persisted reason slots and do not contain credentials.

Added `tests/intelligence/test_llm_fail_closed_rotation.py`,
updated both chat and native System-One exception boundaries, and
regenerated `docs/contracts/module_accountability.json` for all
three governed modules. A Git-blob-identical local copy of
`trading_gym/intelligence/llm_key_pool.py` passed **34 focused tests**
covering auth rotation, hard caps, expiry, invalid 4XX statuses,
non-pooled local/native HTTP response classification, cross-process
persistence, and secret-free storage. Both transports also apply these
terminal response rules when operating without a credential pool.
The entire
exact-head repository suite, provider network integration, and
funded-live qualification remain **unverified / NO-GO**.


## Order evidence and chain identity convergence — 2026-10-15

New implementation: ADR-044 and `trading_gym/convergence/peer_execution_parity.py`
add a **read-only**, immutable, one-order execution-evidence comparison.
It handles exact Decimal fee/cash/position arithmetic; matched versus
drifted broker readbacks; causal event sequence and as-of checks;
identical fill retries and conflicting execution-ID collisions;
post-only maker and IOC partial-fill semantics; and explicit
unsettled-order finality. Broker submission and funded-live authority
are not part of this module.

`trading_gym/production/chain.normalize_chain` now rejects boolean,
fractional, whitespace and noncanonical chain IDs instead of coercing
them to other chains. Canonical integer and decimal-string IDs remain
supported. Pathological Decimal scales and false reconciliation caused
by default-context rounding are denied/corrected respectively.

The additional donor static import/call index covers 3,114 readable
source files in 17 ZIP archives, with the original full member
inventory covering 4,226 entries. This does not establish that all
donor methods are semantically equivalent or accepted for production.

**Verification:** RED-to-GREEN focused local source-mirror tests:
34/34 for peer execution and chain identity, plus 4 RSI boundary tests
against a corrected isolated source snippet (**38/38 combined**).
The current PR already contains the flat-RSI neutralization, so
only protective regression coverage was added for that behavior.
A full repository checkout could not be obtained from the offline
container, and GitHub Actions runs reported job failures with zero
executed steps. Exact-head Python 3.11/3.12, frontend, Rust,
source-manifest and full OpenAPI-generation checks remain unverified.

**Trading safety:** draft PR, unmerged; broker and funded-live **NO-GO**.


## System-1 endpoint/credential/JSON trust boundaries — 2026-10-15

Continuing the draft PR's multi-key and System-1 work, the chat and native
typed transports now propagate URL/DNS/security-policy rejection as
non-degradable `provider_destination_denied` rather than treating it as
an ordinary provider outage that could trigger risk-on rules. Added
independent test cases for both protocols and a policy-adapter fallback
regression.

Provider configuration rejects credential-bearing nested metadata and
unbounded/nonfinite JSON. The provider repository revalidates on save,
including Pydantic `model_copy(update=...)` bypasses. System-1 strict
JSON parsing rejects duplicate keys (including nested keys) and
nonstandard numeric constants instead of silently selecting the final
JSON value. The related governed source-module hashes are updated.

**Verification:** isolated provider metadata checks passed 8/8; extracted
strict JSON parser checks passed 7/7. The new repository tests are
committed but have not been executed against a complete checkout.
Full GitHub Actions and funded-live qualification are still blocked.


## 2026-10-15 skill review and capability-routing convergence

The new [ADR-050](docs/architecture/ADR-050-skill-capability-review-convergence.md)
records a first- and second-order integration of Skillbox / TypeSafe
skill-management semantics into TradingGYM's existing skill owners.

- Hardened `skills/intake.py` against symlink and oversized file input,
  recorded content digests and sanitized risk markers; no skill code executes.
- Hardened `skills/static_assurance.py` and `skills/quarantine.py`:
  canonical SHA-256, read-only capability validation, independent reviewer
  identities, immutable catalogue snapshots and bounded iterative workflow
  DAG verification (duplicate IDs, retry limits, timeouts and cycles).
- Added `skills/capability_selection.py` and a standalone
  `scripts/preview_skill_capabilities.py` CLI. It requires digest equality,
  explicit consent, owner match, independent reviewers, expiry and only
  read-only capabilities. It emits **non-authoritative review hints**,
  never approvals, tool activations, network/model calls or orders.
- Added new focused tests and updated source-accountability hashes. The
  exact GitHub bytes of the four modified/new skill modules were checked
  against their locally verified SHA-256 fingerprints.

**Observed verification:** 17/17 local in-process tests and 6/6 offline
CLI tests passed under Python 3.13.5, with source syntax compilation.
Exact-branch pinned Python tests, formatter, frontend, Rust and venue
reconciliation are still **unverified**.

**Hosted CI:** run
[38791466658](https://github.com/AmirrezaFarnamTaheri/TradingGYM/actions/runs/38791466658)
reported nine failed jobs with **no recorded execution steps** and
three skipped jobs. This is not evidence of a failing or passing
application test. Runners, account configuration and billing policy
must be inspected externally before obtaining usable CI receipts.

**Release:** PR #2 stays draft and unmerged; funded-live **NO-GO**.
Cryptographic reviewer attestation, persistent revocation, role-scoped
research UI and broker emergency-gate qualification remain open.


## 2026-10-15 annualized Sharpe display evidence integrity

The standalone annualized Sharpe report now validates its complete
one-dimensional finite return series and strictly positive integer
annualization frequency. It no longer drops nonfinite observations or
coerces boolean win/loss arrays into performance returns. Eight original
regression cases failed against the prior helper; the valid calculation
remains unchanged. This research-only adjustment does not grant trading
or model-promotion authority.

The combined isolated source-mirror suite passes **112/112 tests**; exact
PR-head full-repository qualification remains blocked by runner allocation.

## 2026-10-15 research walk-forward and bootstrap integrity

- Walk-forward results now reject **any missing/invalid paired window**
  instead of deleting it; research robustness thresholds have validated
  numerical domains, and zero-baseline degradation uses masked division.
- Bootstrap Sharpe intervals reject missing/nonfinite returns, pooled
  strategy arrays, boolean or noninteger sampling/annualization inputs,
  and invalid confidence levels.
- Added regression tests for missing windows, unsafe robustness thresholds,
  divide-by-zero, and bootstrap samples. All changes are research-only and
  cannot promote strategies or authorize funded trading.
- **112/112 local isolated-source-mirror tests passed**, including syntax
  compilation. This is *not* the full checkout test matrix. CI allocation
  and pinned Python 3.11/3.12 qualification remain outstanding.

## 2026-10-15 research Sharpe statistical correctness

ADR-044 records critical research-only statistical fixes:

- Corrected probabilistic Sharpe ratio to use **per-period** SR inside its
  non-normality sampling formula, with annualized benchmarks converted to
  the same sample frequency. On the fixed nine-return regression,
  the old false-confidence value was approximately 1.0000; the correct
  reference is 0.83922934856077.
- PSR refuses missing/nonfinite returns, boolean vectors and pooled
  multi-strategy arrays; it no longer silently removes incomplete
  financial observations.
- DSR now rejects nonfinite Sharpe/dispersion/moments, boolean and
  fractional count inputs, and incoherent sample skew/kurtosis. Its API
  is documented to require **per-period** Sharpe and trial dispersion.
- Permutation p-values now reject nonfinite observed scores, incomplete
  null samples and pooled scenarios; NaN comparisons can no longer
  manufacture apparently significant results.
- Added independent closed-form, unit-invariance and malformed-evidence
  regression tests, and refreshed the research source hash in
  `docs/contracts/module_accountability.json`.

**Verification observed:** an isolated Python 3.13.5 source mirror ran
**112/112 passing focused tests** with syntax compilation. These are
**not** complete repository tests. GitHub Actions run
[#38785467227](https://github.com/AmirrezaFarnamTaheri/TradingGYM/actions/runs/38785467227)
still reports required jobs failed before allocating a runner (zero
executed steps). This needs runner/account remediation and all checks on
one final head before any merge.

The existing turnover-aware optimizer already covers key features in the
Vibe-Trading donor; no duplicate capital-construction owner was introduced.
**Draft PR; funded-live NO-GO.**


## 2026-10-15 verified PR ancestry and funding aggregate reconciliation

**PR consolidation:** GitHub commit comparisons confirm PR #5's head
(`badbe51d6c8a6ccd56de3935a5f0a76f128f3e29`) is an ancestor of
draft PR #2. PR #5 remains formally closed/unmerged, but there are
**zero missing commits** in PR #2. PRs #3/#4 remain formally closed
without merges; their RL-history and broker-pagination guards and tests
are present on PR #2. Another merge is unnecessary. See ADR-049.

**Fixed financial reconciliation defect:** event-level tolerances could
accumulate a larger unexplained settled funding cash balance while
`reconcile_funding` returned `consistent=true`. The module now
emits `funding_aggregate_mismatch:<CURRENCY>` when any
currency-specific net discrepancy exceeds the configured absolute
tolerance. It never nets separate currencies or performs ledger
mutations; it cannot approve funded-live trading.

**Verification:** the focused RED-to-GREEN local production-code mirror
observed a failing cumulative-drift regression, then 3/3 passing.
A fourth separate-currency regression also passed locally (4/4).
Tests and the new source hash were pushed to PR #2. A fresh SHA-256
and CRC audit of **39 donor ZIPs** covered **12,473 file entries**,
**11,802 unique content hashes**, **2,230 inferred nested folder paths**,
three identical-archive pairs and five symlink entries. No donor code was
executed or imported without separate qualification.

**CI qualification remains blocked:** recent GitHub Actions runs on the
PR head fail all nine required jobs before any recorded step or runner
allocation. They do not establish source test outcomes. The exact
Python/React/Rust, accounting/broker, generated contract and security
suites remain **unverified**. PR #2 stays draft; funded-live **NO-GO**.



## 2026-10-15 turnover-aware portfolio and operator research convergence

The Vibe-Trading portfolio comparison has produced an additional
**non-authoritative** method on the canonical institutional construction API:

- `ConstructionMethod.TURNOVER_AWARE` uses a bounded, long-only convex
  mean-variance objective with a full L1 trading-turnover penalty,
  independently checked per-asset bounds and fully accounted prior weights.
  The report exposes one-way turnover, regularization/utility, and a
  SHA-256 prior-holdings receipt, without orders or portfolio mutation.
- Group concentration caps are hard LP/SLSQP constraints, not post-hoc
  annotations. Unassigned assets, unknown sectors, impossible limits,
  non-finite inputs, and incomplete holdings refuse construction.
  Aggregate group exposures and the group-policy fingerprint are reported.
- Source-accountability SHA-256 entries and the OpenAPI method, request
  and result models have been updated. Runtime-schema parity tests were
  authored for the PR, but the full FastAPI generated-file check remains
  unverified.
- The Institutional OS workspace includes an optional "Load turnover-aware
  example" button. It populates a bounded prior-holdings/group-cap
  request without submitting a review; the matching browser regression
  is committed but **not yet executed**.

**Evidence:** focused isolated local optimizer tests: **27/27 passing**
(18 baseline turnover and 9 grouped exposure/feasibility cases).
The canonical Pydantic model integration tests, React/Vitest tests, full
Python matrix, Rust suite, broker fee/funding readback, and exact-source
contract checks **have not passed in the available environment**.
Recent GitHub Actions failures contain zero job steps, which is a runner
allocation/infrastructure symptom, not proof of source compilation failure.
Draft PR #2 and funded-live **NO-GO** remain unchanged.


## 2026-10-15 unified PR and operator-state hardening

PR #5 (`ci/tradinggym-single-pass-coverage`) was merged **into the draft
PR #2 development branch**, not into `main`. A two-parent merge commit
`159ad269a8177c3425e741e18a868bc42d7ab2d8` preserves PR #5's
unique commit ancestry; GitHub comparison verified zero missing commits.
PR #5 is closed **superseded**; PR #2 remains the sole open workstream.

The mixed Ubuntu/Windows Python 3.11/3.12 CI matrix now runs the
non-integration suite once per cell. Coverage is collected during the
Ubuntu/3.12 invocation, without the second full pytest pass and without
PR #5's default-shell-incompatible Bash `[[...]]` test. A new regression
test verifies the mutually exclusive step conditions, matrix and
coverage upload. ADR-048 records the decision and remaining gates.

**Additional operator UI corrections:** the model registry now clears a
stale active-model store when the authenticated API reports an explicit
null; a failed model-status read is labeled **Unable to verify**, not
"No model". While active state cannot be verified, every model row
shows **Unknown** rather than falsely "Inactive", and both the row
activation button and approval-consuming activation control are
disabled. Read-only/backtest functions remain available. React/Vitest
regression cases were added but **not executed on this exact head**.

**Verification:** local PyYAML isolated matrix partition checks pass
all four cases; source branch ancestry and regression-file presence
were verified through GitHub. The 353-entry Python accountability
manifest includes all source paths, and the fingerprint for
`trading_gym/agent/tools.py` was corrected after its privacy change.
A full manifest hash audit and exact-head Python/Windows, React,
Rust, OpenAPI and broker/settlement suites still require real CI runs.

Recent sampled Actions jobs on both previous PR heads failed with
**zero executed steps and no runner assignment**. This is not evidence
of source compilation or integration correctness. Funded-live
readiness stays **NO-GO**; do not merge or bypass safety gates.


## 2026-10-15 additional trading and agent donor convergence

The fourth-pass 17-ZIP donor inventory contains **4,226 members** and
**4,180 distinct SHA-256 payload hashes**. Structural and symbol coverage,
deduplication, per-archive capability decisions and a second-order
convergence backlog are recorded in
`docs/architecture/ADR-043-expanded-strategy-and-donor-convergence.md`
and `docs/plans/2026-10-15-expanded-donor-convergence.md`.
The inventory is not a proof that all donor functions were semantically
validated or legally relicensed.

**Implemented and committed on this PR:**
- Strictly causal, independently bounded Wilder
  ADX/+DI/-DI/DX from OHLC, seeded from observed first-period movement,
  with explicit second-period warmup and no future-candle access.
- `adx` joins the bounded `IndicatorSpec` grammar and registered
  indicator evaluator; the strategy validator accounts for its warmup.
- Two **research-only** example families:
  `adx_directional_trend` and `adx_donchian_breakout`, with position
  fraction <=0.04 and explicit stops. Neither is a model promotion,
  automatic strategy recommendation or funded-trading authorization.
- The evaluator rejects reversed, missing and duplicate timestamped
  bars before calculating rolling signals.
- The published OpenAPI IndicatorSpec enum now includes `adx`, with
  a runtime-schema contract test; module SHA-256 accountability was
  refreshed for all changed `trading_gym/strategy_lab` sources.

**Verification status:** isolated source-mirror checks covered 19/19
indicator, reference-math and chronological integrity cases. The actual
PR-head pinned Python 3.11/3.12 integration checks, React/Rust build,
full OpenAPI generation, SHA manifest generator, broker recovery and venue
settlement tests are **UNVERIFIED**. The full 17-archive per-function
behavioral comparison also remains **UNVERIFIED**.

**Release decision:** PR remains draft and funded-live **NO-GO**.


## 2026-10-15 — 17-archive research and strategy convergence follow-up

The new donor inventory indexes **4,226 files across 617 folder paths**
with 3,113 source-like files and 34,209 extracted symbol declarations.
This is structural/source evidence; no claim is made that all donor
behaviors have been semantically qualified. ADR-043 and ADR-044 record
the first- and second-order adoption decisions.

Implemented on draft PR #2:
- A strict, causal Wilder ADX/DMI indicator, grammar registration,
  two risk-bounded research presets, and a complete 2-period warmup.
- Chronological market-time validation and indicator alias protection,
  preventing price and signal column shadowing.
- Opt-in **next-open, read-only strategy replay** with fees, slippage,
  bounded execution assumptions, deterministic source/input/report
  SHA-256 and transparent unfilled final signals.
- Explicit rejection of contradictory strategy position intents.
- Before-coercion rejection of boolean strategy constants and risk
  limits, plus nonfinite strategy constants and indicator periods.
- Source-accountability entries refreshed for the changed modules.
  No research result, signal or simulated trade grants capital authority.

Fresh *local mirrored-source* verification (Python 3.13.5):
`pytest -q test_causal_replay.py test_grammar_mirror.py
test_indicator_edge_mirror.py` -> **36 passed**.
Vibe-Trading's separately extracted offline optimizer causality test
reported **2 passed**. The broader five-file donor optimizer test
attempt exceeded the local execution time limit; it is **not passed**.

**Unverified on exact PR head:** full Python 3.11/3.12,
Black/Ruff/MyPy, React, Rust, full FastAPI OpenAPI generator, all source
hashes, real exchange/broker execution, funding/fee reconciliation,
independent statistical qualification, and emergency gross-flat readback.
The prior PR's GitHub Actions jobs did not yield a passing suite,
and the current head has no confirmed successful workflow run.

PR remains **DRAFT / UNMERGED**, funded-live **NO-GO**.


## System-1 quota and rotation integrity follow-up — 2026-10-15

The multi-key, cross-worker advisory inference pool now distinguishes
trusted provider unavailability from **corrupted shared rotation state**.
Invalid JSON, symlinks, broken file locks, non-finite clocks and failed
writes raise a typed integrity error that both OpenAI-compatible chat
and native System-One transports convert into `PermissionError`, rather
than allowing a rules/RL fallback to open positions on missing authority
evidence.

Provider HTTP 429 responses are classified using current official API
credit/organization/project spend and usage-limit error codes. A hard
quota blocks both key rotation and risk-on backend degradation; ordinary
temporary project-level 429s still apply a shared cooldown. Transient
remote-protocol disconnects can fail over within the existing
per-decision attempt/deadline budget. New regression cases cover billing
limits, corrupt state, failed locks, invalid clocks and protocol errors
without using or persisting real API secrets.

The affected `llm_key_pool.py`, `llm_backend.py` and
`systemone_native_backend.py` source SHA-256 values were reconciled
to the actual PR head in `docs/contracts/module_accountability.json`.
Operational context, privacy and failure boundaries are documented in
`docs/architecture/SYSTEM1_CONTEXT_AND_KEY_ROTATION.md`.

**Verification boundary:** regression tests are committed but no exact
PR-head full Python matrix or live provider integration suite has passed.
The container cannot clone the GitHub source checkout; earlier
GitHub-hosted workflows failed before runner allocation, and the latest
head has no completed, source-executing CI evidence. Do not merge or
allow funded-live deployment on the basis of these source edits.



## System-1 fleet follow-up — 2026-10-15

Multi-key rotation now retains a provider-level 429 cooldown across key-list
changes and model variants instead of resetting when a new key is added.
Quota terminal signals in `error.type` and provider-declared shared
rate-limit scopes also halt rotation. Tests cover the same-data-home
cross-configuration state, per-key continuity and hard-quota boundaries.

Both chat and native System-1 apply the operator's signed leverage window
before/after model inference. Invalid risk bounds deny prior to network I/O;
prohibited long/short recommendations become HOLD, and asymmetric short-only
windows remain expressible. The live System-1 packet now rejects irregular
mark-update returns and empty completed-bar histories even with a syntactically
valid candle fingerprint. These changes add targeted regression coverage;
isolated reproductions and the new pure signed-risk helper's 10 passing
reference checks do not certify exact-head repository-wide integration,
hosted-provider security or funded trading. The PR must stay draft/NO-GO.



## System-1 context cadence, qualified review and non-mutating evidence (2026-10-15)

The existing multi-key API-credential pool, global provider request-admission
ledger and native typed System-One path remain advisory and opt-in.

This follow-up repairs three integration risks:
- Weak directional fast-model predictions are now `UncertainDecision`,
  not provider outages. Only another qualified fast model or a strict,
  confidence-thresholded deep reviewer may escalate them. Otherwise the
  decision is neutral HOLD, never automatic risk-on deterministic rules.
  Malformed strict-model responses are non-degradable evidence failures.
- Validated completed OHLCV bar closes now travel from live feature assembly
  into the market context. Current live marks are labeled as quotes; return
  sampling distinguishes completed bars from legacy mark-price updates.
  Historical simulations retain their own explicit provenance.
- Optional long-term experience lookup is genuinely read-only and verifies
  anchors even for missing/emptied journals. Opted-in short-term settled
  memory requires an explicitly connected source: standard paper/live
  entrypoints do not yet supply one, and startup fails rather than claiming
  history it does not have.

Focused tests were added for uncertain System-1/native directions versus
risk-on fallbacks, strict deep-model reviewers, malformed JSON, provenance
sampling, bounded memory consent, and anchored journal integrity. New Python
module accountability SHA-256 values were regenerated and checked from the
committed sources. **The exact repository test matrix is NOT certified**:
GitHub Actions runner execution is still blocked, and this environment
cannot clone the entire GitHub checkout. These changes do not measure
model calibration, live alpha, benchmark latency, or external provider
contract reliability. PR #2 stays draft; funded live remains **NO-GO**.



## 2026-10-15 venue-local settlement and anti-hindsight evidence

ADR-046 records two more tested financial-data correctness repairs in
`trading_gym/accounting/settlement.py`:

- **Due date is venue-local:** pending obligations use their individual
  settlement calendar timezone to determine overdue dates, not the UTC
  date of the report. New York and Tokyo correctly disagree on opposite
  sides of their own midnight.
- **As-of evidence is causal:** future trades and future confirmed
  settlements are refused in a historical snapshot; confirmed
  settlement cannot precede trade. Historical `FAILED`/`CANCELLED`
  transitions still need timestamped event-ledger evidence before
  retrospective reconstruction can be certified.

A local, byte-verified copy of the production settlement source
reproduced **three RED** timezone failures and **three RED** future-event
failures. The final local settlement tests passed **7/7**, and four
existing-contract scenarios passed. Across the entire available
source mirror, **73/73 tests passed** with clean syntax compilation.
The final pushed settlement Git blob matches the local tested file
exactly; source-accountability SHA-256 has been refreshed.

No original broker or custodian records were connected. This does not
verify complete source-history independence, all Python 3.11/3.12
repository tests, CI runners, multi-currency FX, actual settlement,
frontend, Rust or gross-flat recovery. PR stays draft; funded-live
remains **NO-GO**.


## 2026-10-15 portfolio post-fee concentration correctness

An additional donor-parity review of Vibe-Trading's turnover-aware,
rebalance-snapshot and execution-evidence methods uncovered a **real
TradingGYM risk-control defect**: the existing dry-run rebalance planner
computed asset/cash weight and maximum-concentration checks relative to
starting equity, even after deducting estimated transaction fees.

On PR #2, `plan_rebalance` now computes a final fee-adjusted projected
equity denominator for all projected position weights, cash weight and
target residuals. Non-positive projected equity is explicitly refused.
The original equity/turnover reporting contract and non-authoritative,
non-mutating planner semantics remain unchanged.

**Behavioral TDD:** a byte-verified copy of the exact GitHub module
reproduced two RED failures (a 90%-reported allocation that was
actually 98.9% after fees, and a falsely feasible zero-equity
liquidation). The corrected module passed the three focused tests,
then an additional two-asset balance-sheet reconciliation test.
Three reproduced existing portfolio tests also passed. The local
isolated source mirror reported **62/62 passing**, plus clean Python
syntax compilation; the pushed module's Git blob hash matches the
tested source. See ADR-045.

**Still unverified:** all pinned Python 3.11/3.12 suites, exact-head
CI, source-accountability generator, full FastAPI OpenAPI generation,
real venue execution and fee settlement, frontend/Rust, and
independent broker recovery. No merge or funded-live authorization
is implied. PR #2 remains draft and **NO-GO**.


## 2026-10-15 all-archive reconciliation and second-order convergence

ADR-045 reconciles all uploaded ZIPs into one read-only donor inventory:
**39 archives, 12,473 file entries, 2,238 nested folder paths,
11,802 distinct file-content SHA-256 hashes**, 304 cross-archive
same-content hash groups and three exact duplicate archive pairs.

The local audit retains every member/folder path and SHA-256, without
extracting or executing donor code. This is complete structural
enumeration, **not** complete method-by-method semantic qualification.

Actual committed convergence this pass includes the causal ADX family
and research presets, strict candle timestamp validation, fixed RSI
and stochastic edge behavior, sealed paired strategy comparisons,
bounded CLI and cost-scenario sensitivity. The evidence is
descriptive and permanently non-promotable; existing capital/
broker/execution governance is unchanged.

**Verification:** 55/55 focused tests passed in a Python 3.13.5
source mirror. Full exact PR-head Python 3.11/3.12 checks, static
format/type checks, app-import integration, React/Rust, source-manifest
generator, OpenAPI generator and broker-fault qualification remain
unverified. PR #2 remains draft and funded-live **NO-GO**.


## 2026-10-15 second-order execution-cost robustness

The research-only `trading_gym/experiments/cost_sensitivity.py` module
adds deterministic multi-scenario fee/slippage comparison, ranking
reversals and hashed per-scenario evidence. It reuses the bounded,
aligned paired-holdout comparator without extending live order or
portfolio-authority permissions. Its independent eight-case test
suite passed in an isolated source mirror.

Across the paired scorecard, CLI, cost scenarios, RSI/stochastic
regressions, prior ADX indicator and historical timestamp tests,
**55/55 focused isolated tests passed** under Python 3.13.5.
The newly pushed cost-sensitivity module and tests match the
locally tested file bytes by Git object SHA.

Source-accountability now includes the new research module;
repository-wide generator and exact-head CI checks are still
**unverified**. All reports remain nonpromotable, real fill costs
are not known from simulations, and funded-live remains **NO-GO**.


## 2026-10-15 paired research and strategy-indicator follow-up

ADR-044 implements a higher-level **offline paired strategy comparison
plane**, based on AI-Trader's experiment scorecards and Vibe-Trading's
turnover/optimizer causality principles. Its sealed market and strategy
fingerprints, aligned holdout bars, explicit time boundaries, cost deductions,
descriptive block-resampled uncertainty, report hashes, nonpromotable
status, and bounded sanitized CLI are committed to PR #2.

Also repaired two method-level indicator defects: flat prices now produce
neutral RSI=50 instead of a false oversold 0, and stochastic `%D` warmup
matches its user-selected smoothing window rather than always using three.
The strategy-indicator implementation hash and new comparison module
fingerprint were synchronized in the source-accountability manifest.

**Verification:** a local Python 3.13.5 source mirror recorded RED failures
for the missing research scorecard, RSI/stochastic defects, malformed CLI
input, and compound-return overflow. The final combined focused regression
suite reported **47/47 passing** with Python syntax compilation successful.
The newly pushed comparator, command, and both new test files were verified
byte-for-byte equal to the locally executed files using Git blob hashes.

This evidence does **not** cover the full TradingGYM checkout, Python
3.11/3.12 compatibility, UI/Rust builds, broker settlement, exact-head
CI, or economic validity of backtest ranks. Source inventory and semantic
acceptance remain separate. **Draft PR; funded-live NO-GO.**


## 2026-10-15 expanded donor strategies and second-order audit

Seventeen newly supplied ZIP archives were inventoried without executing
any peer setup script or live trader: **4,226 entries, 617 nested folders,
4,180 unique payload hashes**; 3,113 source-code files were indexed and
34,209 AST/lexical symbol declarations extracted. Three ZIPs repeat
identical prior snapshots and were not merged again. See ADR-043 and
`docs/plans/2026-10-15-expanded-donor-convergence.md`.

**Implemented on PR #2:**
- Causal Wilder ADX, +DI, -DI and DX calculations with strict value,
  component, and two-period warmup semantics. Corrected first-period
  smoothing to the original arithmetic Wilder seed.
- Extended indicator grammar, evaluator registration and strategy workload
  planning. Added two risk-bounded research-only declarative presets:
  `adx_directional_trend` and `adx_donchian_breakout`.
- Rejected reversed, duplicated and absent `DatetimeIndex` candle
  timestamps before evaluating rolling indicators or crossover rules;
  ordered and legacy RangeIndex input remains accepted.
- Regenerated module-accountability entries for the changed strategy
  sources, including the new ADX module, and updated the IndicatorSpec enum
  in the published OpenAPI snapshot.

**Verification:** 15/15 isolated ADX tests passed, including the
hand-calculated Wilder recurrence and prefix/hindsight checks. Four separate
timestamp integrity tests passed after reproducing 3 RED failures.
A numerical synthetic regime reversal demonstrated ADX-filtered entries
and exits without implying profitability. A **full exact-head** checkout
was unavailable to run the Pydantic strategy-grammar, frontend, Rust,
broker, all-hash and OpenAPI generation suites; those are still
**unverified**, not green.

The archived source index and source path-based candidate register are
not proof that every peer method has been individually behaviorally
audited. Vibe-Trading's 2,882-member tree and other independent agent
runtimes still require deeper source-to-target acceptance.

PR #2 remains draft and unmerged. Funded-live remains **NO-GO**.


## 2026-10-15 typed-signal and provider contract follow-up

ADR-042 records additional source-level fixes on this draft PR:

- `DecisionSignal` rejects boolean, nonfinite, string-valued or
  non-enum advisory fields before fusion or policy use. The policy
  adapter prevents this typed domain refusal from invoking fallback.
- Native System-One's POST-only health check requires a method-specific
  `Allow: POST` on HTTP 405; an arbitrary 405 is not model readiness.
- Malformed JSON, non-object envelopes and oversized successful typed
  responses are terminal evidence failures rather than recoverable
  provider outages that could open a fallback trade.
- Restored Pydantic `default: null` for four nullable ProviderConfig
  fields in the committed OpenAPI snapshot; added schema-regression coverage.
- Refreshed SHA-256 accountability for all Python modules changed in
  this follow-up.

**Observed verification:** the isolated Python 3.13.5 mirror passed
**16/16** focused behavioral cases and compiled without syntax errors.
This is **not** the complete or pinned exact-PR-head test matrix. The
module manifest covers 346/346 paths with no duplicates, but the
all-hash generator and full FastAPI OpenAPI generator still require CI.

The PR remains draft and unmerged. Model calibration and funded-live
trading approval remain **NO-GO**.


## 2026-10-15 advisory authority and closed-bar follow-up

A new ADR-041 and targeted regression coverage were added to PR #2:

- Reject structurally malformed or contradictory model output as a **terminal
  authority failure**, not a trigger for a new risk-on fallback. Recoverable
  provider/model outages retain the explicit configured degradation chain.
- Native System-One's malformed probability envelope is non-degradable;
  recognized low confidence remains a recoverable signal.
- For live System-1 evidence, `bar_open + interval` must not be later than
  `now`; previously permitted future-closing bars no longer claim
  `verified_closed_bar`.
- Model-routing previews reject unknown request-budget capacity.
- Repaired the source manifest's two omitted modules:
  `trading_gym/intelligence/llm_key_pool.py` and
  `trading_gym/intelligence/system1_context.py`. The branch has 346 Python
  source modules under the accountability generator's roots.

**Verification:** RED-to-GREEN local source-mirror tests reproduced the
failure modes; the focused final runs report 66/66 broader isolated tests,
13/13 policy/freshness tests, and clean syntax compilation. **These are not
exact-head full-repository CI, Rust, browser, or broker tests.** The latest
observed PR head has no confirmed successful GitHub Actions workflow run.
Funding/execution authorization is unchanged: **NO-GO**.



## 22-archive native System-One and holdout-verification convergence — 2026-10-15

The latest user-supplied 12 archives extend the earlier ten to **22 ZIP files,
8,247 file entries, 1,574 nested folders, 7,923 unique SHA-256 payload hashes,
and 280 identical-content groups**. See ADR-039 for individual donor
subsystems, actual adoption decisions and second-order product-plane analysis.
The metadata register is exhaustive; method-by-method semantic proof,
compilation and legal qualification of every donor are **not** claimed.

Implemented on this draft branch:
- `ProviderConfig.wire_protocol=systemone` is opt-in; CLI providers are
  excluded, native POST-only route health is distinguished from chat `/models`,
  and regular LLM/chat mode rejects typed providers instead of issuing a
  malformed chat call.
- `NativeSystemOneDecisionBackend` and strict `systemone_protocol` accept
  bounded, provenance-checked `system1.market.v1` structured JSON state;
  send two independent choice/noul questions in one inference request; verify
  finite normalized distributions, winning labels, operator confidence,
  risk-off veto and local leverage ceilings. Key rotation, attempt budgets,
  DNS/redirect/proxy protection and immutable authority gates are preserved.
- The policy adapter validates finite risk limits, named feature values,
  boolean live flags and realistic drawdown **before** any backend fallback.
- `systemone_evaluation` and `scripts/evaluate_systemone.py` form an
  offline holdout assessment plane: replay hashes, label-time checks, Brier,
  ECE, coverage, accepted accuracy and latency p95; no raw context, prompts,
  user keys or inferred trading qualification in reports.
- Native advice now enforces response-model identity, explicit weak HOLD,
  risk-limit and neutral-action invariants before fallback. Native global
  scorer 429s cannot be evaded through per-key rotation.
- Working-memory P&L disclosure is separately opt-in, independent of
  long-term experience lessons; both consent flags are visible to operators.
- The legacy model-promotion helper now checks strict hexadecimal SHA-256
  evidence syntax, distinct normalized principal identifiers, and strict
  integer revision compare-and-set, without replacing authenticated
  governance approval or live-execution qualification.
- The 1 MiB committed OpenAPI artifact has had the `ProviderConfig` schema
  updated with native protocol and fleet fields; full exact-app generation
  remains **UNVERIFIED** due the unavailable checkout/runner environment.
- An offline deterministic model capability/cost routing preview rejects
  unknown prices, exhausted caps, protocol/context mismatch and unconsented
  remote context. The operator workspace now lists six non-mutating,
  disabled-by-default local/native/hosted provider templates. Neither
  feature grants provider activation or trading authority.
- Explicit low-confidence HOLD remains zero leverage in both native typed
  and legacy fast-chat System-1 paths, rather than triggering a risk-on
  fallback. The chat-backend exact-head regression remains unexecuted.

An isolated Python implementation suite (controlled external dependencies)
passed **64/64 tests**, including the model-identity, risk-cap, and explicit
HOLD regression cases. The `jev-model-router` donor's self-contained Python
suite passed **60 tests with one skip**. These are not a green
repository-level certification. The exact PR-head Python 3.11/3.12, frontend,
Rust, OpenAPI generation, source-manifest verification, broker integration,
real Go System-One inference and capital emergency gates are **UNVERIFIED**.
GitHub Actions workers have previously reported failures without allocating
any executable job steps. PR remains draft. Funded/live **NO-GO**.



## Ten-archive convergence and operator observability — 2026-10-15

The existing draft PR #2 was augmented without merging, changing `main`,
or authorizing funded live operations. Ten user-supplied source ZIP archives
were indexed at metadata level (4,210 member files, 1,124 nested folder paths,
3,929 distinct SHA-256 hashes); duplicate contents and broad integration
seams are recorded in ADR-038. Classification does **not** mean every donor
function has been semantically reviewed or ported.

Focused changes:
- A validated order-book snapshot now rejects non-finite levels, boolean
  sequences/quantities, duplicate price points, crossed books, and unbounded
  level generators; a failed feed stream invalidates sequence state and
  mandates a fresh full snapshot. An explicitly read-only helper calculates
  finite spread, book-depth imbalance and microprice diagnostics without
  granting any trade authority.
- Offline `scripts/source_impact.py` builds a bounded digest-bound,
  lower-bound file-import graph for Python, JS/TS and limited Rust and returns
  transitive source impact with file hashes; it does not pretend to be a
  complete compiler, call graph or deployment authorization gate.
- The existing but previously unmounted `IntelligencePanel` is now
  accessible under the four existing workspaces. Its read-only status
  displays System-1 roster, memory consent posture and provider request
  admission. Exhausted or corrupted provider budgets report unavailable
  and cannot appear healthy. No credentials or ledger file paths are returned.
- Added targeted tests for graph traversal, feed poisoning and oversized
  inputs, independent view navigation, sanitized API data and operator UI.

**Verification:** the isolated source mirror on Python 3.13 passed
**21/21 focused tests** (15 market-book, 6 source-impact) with Python syntax
compilation. These tests are not the complete exact-head repository suite.
Frontend Vitest/build, real provider contracts, Rust, security, broker,
Python 3.11/3.12 and code formatter qualification remain **unverified**.
Hosted CI run [#38648120845](https://github.com/AmirrezaFarnamTaheri/TradingGYM/actions/runs/38648120845)
marked nine required job groups as failed with **zero reported execution
steps**; runner allocation/environment must be restored and all checks
rerun on the final head before any merge or release.

Capital ownership, measured real-venue settlement/fill/fee reconciliation,
distributed fencing and independently verified gross-flat recovery remain
funded-live NO-GO requirements. ADR-038 describes the separate second-order
architecture and explicitly deferred donor imports.


## System-1 context and provider-key fleet — 2026-10-15

The existing LLM path had one environment-key reference, sent a truncated
4,000-character market JSON document, and did not supply the agent's own
working/experience memory or trustworthy live-bar source provenance.
This branch now adds:

- per-provider ordered credential *environment references* with bounded,
  file-locked process-fleet rotation, key-independent cooldowns and
  sanitized HTTP failure handling; no secret values are persisted;
- a short, total HTTP request deadline and retry budget, no redirect/proxy
  inheritance, fail-closed 403 handling, project-level 429 cooloff by default,
  and optional per-key rotation only for providers with documented
  independent rate-limit buckets;
- a deterministic, bounded, source-hashed System-1 market packet combining
  real account/risk inputs, recent returns, selected normalized closed-bar
  indicators, verified timestamps/freshness and optional settled memory;
- strict four-field JSON response validation, low-confidence escalation,
  conservative leverage cap, configurable fast-model fleet and optional deep
  LLM/rules fallback without laundering PermissionError into authorization;
- dry-run live model-free policy integration, read-only System-1 status, and
  explicit refusal to admit unqualified fast models to funded live execution.

Focused isolated red/green verification covered **12 key rotation/failover**
and **7 context** cases (19/19 passed on Python 3.13). Dedicated
repository-level tests were added, but full Python 3.11/3.12, frontend,
Rust, provider-integration, formatting, security and broker suites remain
**UNVERIFIED** because hosted GitHub Actions jobs are not executing.
This is not a measured low-latency/alpha or real-provider qualification.
See ADR-036 for operational configuration and context contracts.



## Generation-fenced live panic acknowledgement — 2026-10-15

Reproduced three control-plane hazards: a worker without a live adapter
could erase the shared panic signal, a second panic written during the
first flatten could be deleted by the first acknowledgement, and a
dry-run worker could retire the global signal before the actual live
executor stopped. A panic acknowledgement now requires an admitted
live-execution owner and checks a bounded snapshot of the exact on-disk
signal generation (device, inode, modification time, size, SHA-256)
under the same process lock used by signal writers. New or malformed
signals are retained; no broker mutation is performed by the
acknowledgement helper. The dry-run adapter also requires read-only
proof of zero gross broker exposure and no open orders to report a
verified flat result, but is never allowed to retire a global panic.
Targeted regressions cover adapterless observers, same-content
replacement, newer panic generations, bad signal records and normal
acknowledgements. Isolated reference suites passed: 6 panic cases
and 2 dry-run cases; **full exact-head CI is still unverified** and
funded-live execution remains NO-GO.



## Principal-bound emergency halt state — 2026-10-14

Resolved an identity spoofing gap in `POST /api/v1/production/emergency`.
The previously required `X-Actor` / `X-Approver` header strings and the
shared `APPROVAL_API_KEY` did not bind either human to an authenticated
principal. The gateway now requires distinct registered actor-bound
credentials (`X-Actor-Key` and `X-Approver-Key`) with operator and approver
roles, the independent approval factor, and the existing gateway key.
A single dual-role principal cannot self-approve. Request body and
domain mutations also reject boolean/revision coercions and unbounded
durable fields. The CORS header allowlist and exact OpenAPI contract
were updated together. Added principal-bound integration and domain
regressions; 16 isolated reproduction cases passed after the change,
but full exact-repository CI and broker qualification remain unverified.



## Source ZIP confidentiality and inventory — 2026-10-14

The source ZIP builder now reads the Git-tracked inventory instead of
recursively archiving the entire working directory. Untracked `.env`,
wallet-secret, and temporary files are excluded; tracked dotenv secrets,
symlink-backed tracked paths, missing sources and non-Git invocations fail
closed. Four isolated Git-checkout tests passed on the revised builder
after reproducing the pre-fix `.env` leak. SHA-256 of the output ZIP is
computed in a bounded stream rather than reading the whole archive into
memory. The exact full-repository build and hosted CI matrix have not
yet executed successfully because runners remain unavailable.



## Atomic authorization grant transitions (2026-10-14)

The ledger's existing chain lock now guards lifecycle preconditions *after*
the authoritative disk reload. Concurrent handles cannot append a stale
second revocation or breach transition, and grant ID collisions fail under
that lock. Semantic replay rejects duplicate IDs and impossible repeated
revoke/breach events, even with well-formed hash links. Tests cover stale
revoke, stale breach, replay-safe idempotence, collision and valid parallel
issue paths. Isolated reproductions were red on three cross-handle defects
and green on 25 cumulative reference tests. The full Python matrix
remains unverified pending CI runner allocation.



## New-grant constraint issuance validation (2026-10-14)

New grants reject invalid, duplicated or non-finite/negative constraint
ceilings and non-canonical names before creating signed journal records.
Python boolean TTLs no longer count as one hour. Historical malformed
records are still parseable and fail closed during evaluation, rather than
being silently rewritten. Isolated red/green tests exposed eight input
acceptance bugs and passed 21 accumulated scenarios after correction.
Repository tests were updated and expanded; the full Python matrix remains
unverified while GitHub-hosted jobs receive no runners.



## Authz journal truncation/corruption integrity (2026-10-14)

Fixed hash-ledger replay accepting an entirely emptied journal when its
anchor remained, and the related missing-journal case. Malformed or
oversized anchors and non-object journal entries now surface typed
`GrantLedgerIntegrityError` rather than unexpected JSON/attribute
exceptions. Read-time symlink defenses and strict anchor shape checks
were added. Regression tests cover full truncation, missing journal,
malformed anchor records and unchanged valid replay. A full in-repository
pytest matrix is still not certified while hosted runners execute no steps.



## Ledger denial-composition, numeric and temporal safety (2026-10-14)

New regressions ensure overlapping grants cannot bypass bounded notional or
other explicitly defined ceilings, omitted observations cannot silently allow,
and non-finite/malformed observed values deny at the ledger boundary. Both
`check` and `require` use deny-overrides-allow; unrelated grants remain
nonvetoing. Future-dated grants cannot be used before issuance and stale
in-process Grant handles no longer retain authority after revocation.
Isolated reference tests reproduced four defects and passed eight cases after
repair. Full repo CI awaits actual runner allocation and execution.



## Feature fixture integrity (not synthetic lake manifests) — 2026-10-14

A new verifier and regression suite check all tracked
`trading_gym/data/*_5m_features.parquet` fixtures against their existing
`SHA256SUMS` hashes. The pinned feature fixtures differ from runtime
`BTC/5m/part_*.parquet` lake partitions, so no fake 28-symbol Merkle
manifests were generated. Four isolated verification tests passed,
covering complete, mutated, missing and untracked fixtures; the exact
repository snapshot integrity test remains part of the hosted suite
awaiting CI execution. See `docs/architecture/FEATURE-FIXTURE-INTEGRITY.md`.



## Operator navigation workspace and scroll containment — 2026-10-14

The operator sidebar now groups all twelve existing panels into four
labeled workspaces (execution/risk, research/playground, model/agent forge,
and governance/system). The desktop sidebar has its own bounded vertical
scrolling region, while content scroll remains independent and the footer
stays visible. Mobile navigation keeps a horizontal scroller without
expanding the entire app. Tab IDs and panels remain unchanged; keyboard
roving focus now traverses grouped tab buttons. A Vitest regression covers
workspace membership, all destinations, and cross-workspace keyboard
navigation. Emergency authorization flows are unchanged; no unauthenticated
one-click funded mutation was introduced.



## Canonical backend training default — 2026-10-14

The backend Pydantic `TrainingStartRequest.config_name` default now selects
the tracked `training_local.yaml` profile, matching the repaired frontend
Training Lab. Generated OpenAPI schema metadata and a release-contract test
were updated together to prohibit advertising a nonexistent training
default. This change does not relax the required `model_version` field.



## Native crypto evidence qualification gate — 2026-10-14

`trading_gym/crypto/assurance.py` now explicitly refuses to mark a native
cryptography manifest ready for human review if its build was not completed,
source SHA-256 is malformed, compiler identity is missing, or vector count is
not a positive integer. Boolean and incompatible numeric shapes cannot
silently satisfy these checks. The results remain advisory only and
deliberately retain `constant_time_claim=False` and `audit_claim=False`.
The missing `gm-crypto-rs` source remains unrecovered and its intentionally
inert placeholder is not used as a cryptographic implementation. This
change does not provide native-crypto audit evidence or live execution
clearance. A local isolated red/green test cycle showed 12 failures on
the previous helper and 17 passing cases after the fix; full CI remains
a separate release requirement.


## Authz operator warning and resilient training telemetry — 2026-10-14

The authorization panel now shows an actionable warning when the
backend reports outstanding legacy high-risk wildcard grants, and an
explicit unknown state if a valid ledger response omits that count.
Training Lab WebSocket log ingestion rejects malformed frames and
unknown record shapes before adding them to the log store, while
scroll-to-bottom remains safe where DOM scrolling is unavailable.
Dedicated frontend regressions were added; full Vitest verification
awaits restored GitHub Actions runner allocation.



## Canonical runtime and published API identity — 2026-10-14

The FastAPI application's `version` now reads the canonical
`axiomforge.__version__`, aligning it with the published OpenAPI
`info.version`, Poetry package, frontend package, and release manifest.
The README heading now identifies this reconstructed release; references
to 31.7.1 remain historical remediation checkpoints. A release-identity
regression checks the published contract and README. The generated
OpenAPI body is otherwise unchanged. This is a metadata correction,
not an API route or protocol migration. Exact-head generated-contract
and CI verification still require runnable hosted checks.



## New delegated-scope guard — 2026-10-14

Issuing a new wildcard grant across live trading, production or
economic/payment/venue operations is prohibited at `GrantLedger.issue`.
Explicit scopes and non-funded `trade.paper.**` remain supported.
Previously signed grants retain their existing grammar, so old journals
can be verified and revoked; API authz health now counts legacy broad
grants needing review before any funded use. Tests cover direct ledger
issuance, API issuance, narrow grants and historical parse compatibility.
This does not replace real broker admission/settlement qualification.



## Generated artifact hygiene — 2026-10-14

Removed three empty lock and four generated Rust exit-code artifacts
previously committed under `trading_gym/data/manifests/` and
`rust/axiom_kernel/`. Added narrow ignore and source-release exclusion
rules with an archive regression test, leaving `poetry.lock`,
`Cargo.lock`, the 28 small Parquet test fixtures, and historical
reconstruction CSV evidence intact. No claim is made that zero-byte
files themselves caused deadlock; `filelock` permits pre-existing
lock files, and that assertion in the attached audit was unproven.
No protected `SHA256SUMS` source entries referenced removed files.



## Training start and browser release identity — 2026-10-14

The Training Lab now supplies the required `model_version` in
`POST /api/v1/train/start` and requires an explicit, validated registry
identifier before enabling the start action. Its default config was the
nonexistent `training_t4.yaml`; it now chooses tracked
`config/training_local.yaml` and still supports `training_rtx3050.yaml`.
New Vitest cases exercise form input and JSON payload serialization.
The operator footer derives its version from `frontend/package.json`
instead of an obsolete hard-coded label. The backend FastAPI metadata,
generated OpenAPI and complete hosted frontend matrix remain separately
unqualified, as do live-capital deployment gates.



## Risk-observation integrity and availability-probe isolation — 2026-10-14

The advisory risk guard now rejects non-finite configured position caps
and denies non-finite position/drawdown observations before invoking
the wrapped strategy. Its authority denial propagates through the
planner instead of permitting a fallback arm. A transient availability
probe error is now benched as a provider failure, while explicit
`PermissionError` remains an unbypassable authorization denial.
Focused regression cases were added in `test_factory_budget.py` and
`test_planner.py`. Source-mirror checks passed; full GitHub Actions
remains blocked before runner allocation.

## Vote-mass overflow and direction/exposure invariants — 2026-10-14

Additional advisory-plane safety cases revealed that individually finite
vote weights can overflow the aggregate, converting `inf / inf` into a
NaN-confidence directional vote. The fusion engine now escalates non-finite
vote and leverage aggregates to flat; active proposals with contradictory
action direction and signed leverage also escalate. Disabled (zero-mass)
arms cannot veto healthy candidates. Deterministic regression tests cover
these cases. This does not replace downstream execution risk controls or
qualify the blocked hosted CI matrix.

## Caution sizing and immutable-weight interoperability — 2026-10-14

An additional agent hardening correction preserves copying and dataclass
projection of validated read-only fusion weights, including pickle roundtrips.
The cautious vote threshold still tightens, but the leverage multiplier is
applied only once after the fusion ceiling, rather than squaring the factor
for limit-clamped decisions. Regression tests have been added to
`test_agent_fusion.py` and `test_planner_memory.py`. Isolated reproduction
checks passed; the exact PR-head CI suite is still blocked by runner
allocation. No funded venue execution was performed.

## Advisory-decision authority and finite-value guards — 2026-10-14

This addition to PR #2 tightens the agent decision boundary without
claiming funded-live readiness. An arm-level `PermissionError` is
now propagated instead of being interpreted as a recoverable provider
failure that could select another arm. Fusion configuration rejects
non-finite limits, and validated vote-weight inputs are snapshotted
against later mutation. Working-memory settlements reject non-finite P&L
before updating a cycle; caution state/policy parameters reject non-finite
or invalid severity settings, including high-severity policies that
would loosen lower-severity safeguards.

Regression tests are included under
`tests/agent/test_decision_authority_invariants.py` and
`tests/agent/test_planner.py`. This source change does **not** qualify
the exact-head Python, frontend, Rust, or broker integration matrix;
the original CI and live capital authority gates remain mandatory.


## Resilience and capacity pass — 2026-10-11

The latest audit branch adds **generation-fenced provider and venue recovery** (stale completions cannot reverse a newer circuit decision), **bounded market-data fanout and cached-frame memory**, per-call fast/balanced/quality routing, immediate re-selection after new provider registration, and **intelligence fallback that preserves policy denial and input-integrity boundaries**. The core changes are exercised by a **61-pass targeted local Python 3.13 suite over reconstructed changed-module mirrors**, including deterministic RED-before/GREEN-after races. These results are **not** an exact-head supported-runtime full matrix. See [Resilience operating contract](docs/architecture/RESILIENCE-OPERATING-CONTRACT.md).

The **production-live NO-GO** remains: shared authoritative capital/settlement, real broker contract and multi-leg emergency recovery, hosted CI runner availability, and pinned Black/type/frontend/Rust/system-level integration verification remain unqualified. No guarantee of zero remaining races, failures or future improvement is asserted.

**Status: draft / not cleared for merge or funded live trading.** This section tracks [PR #2](https://github.com/AmirrezaFarnamTaheri/TradingGYM/pull/2), not the earlier reconstructed-source release whose historical qualification record follows.

The branch includes execution/outbox, broker readback, treasury, emergency, RL observation boundaries, API supervisor ownership, WebSocket handshake and authorization-log hardening. The separate RL and emergency-pagination draft PRs were integrated into PR #2 and closed as superseded. They were not merged into `main`.

**Independent verification:** focused Python 3.13 tests with local dependency substitutions passed for new WebSocket, supervisor, RL-boundary and emergency-page scenarios. This is not a supported-environment release matrix. An earlier supported-environment CI revision passed approximately 1,750 tests per matrix leg but had two source-accountability manifest failures. That result applies to that older revision only.

**CI at this checkpoint:** the latest GitHub Actions job matrix failed before runner allocation (zero assigned runners, zero executed steps, logs unavailable), so no current-head Python 3.11/3.12 Windows/Linux, Rust, type, frontend or security claim is made. The temporary one-time formatter was removed after it repeatedly failed to obtain a runner. **Black 24.10.0 formatting is still required** on the current checkout; the committed module-accountability manifest was recalculated from the source files, but must be regenerated after any formatting change.

**Blocking external-release obligations:** a single authoritative capital reservation / settlement owner, actual venue fill/fee/collateral reconciliation, broker contract qualification and fault-injection, gross-neutral multi-leg emergency close design, supported Python and frontend matrix checks, and a verified human review. Without all relevant gates, funded execution remains prohibited by operating policy; the repository does not claim that source hardening alone enforces these external prerequisites.

To complete a source-quality pass in a supported Python 3.12 checkout:

```bash
poetry install --with dev
poetry run black .
poetry run ruff check .
poetry run flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
poetry run python scripts/audit_module_accountability.py
poetry run python scripts/audit_module_accountability.py --check
poetry run pytest -m "not integration" -q
cd frontend && npm ci && npm run test:ci && npm run build
```

These commands are **instructions, not claimed executed results**. Re-run the full GitHub CI matrix and review the exact resulting commit before merging. Restoration of GitHub-hosted runner availability is an external prerequisite.

---

# Trading Gym / AxiomForge 31.7.0+reconstructed.1

## Release identity

This is the **final reconstructed converged source release** built on the uploaded Trading Gym 11.0.0 source archive (`SHA-256 7a3e110e6e046376eb9866ccc4397ada282473ba66f29bb56ec01fd6ae251bb8`). It reconstructs the latest recovered Trading Gym/AxiomForge behavior and target ownership through the 31.7.0 remediation line.

It is **not** represented as the byte-identical historical 31.7.0 source tree. Later source bytes were not retained; later adoption ledgers, qualification artifacts, target paths, invariants, acceptance evidence, and the intact v11 source were used to re-derive target-native implementations.

Recovered lineage: `23.1.0 -> 24.0.0 -> 25.0.0 -> 26.0.0 -> 27.0.0 -> 28.0.0 -> 29.0.0 -> 30.0.0 -> AxiomForge 31.7.0`.

## What is converged

- A single governed live mutation path: startup authorization/live consent -> payload-bound `LiveExecutionCoordinator` -> Aban venue transport -> SDK.
- Exact-flat execution accounting for true flatten requests.
- Authoritative Aban policy/config identity with duplicate limiter/retry authority removed.
- Durable emergency halt integrated with the sole live coordinator and authenticated API/operator surfaces.
- Governed model promotion, decision lifecycle, regime-vote rationales, configuration hashing, noncustodial wallet/chain registry, and Trust Wallet projection.
- Generated OpenAPI truth, immutable GitHub Action pins, blocking release gates, source-release identity, executable-bit verification, and module accountability.
- All 25 reconstructed late-wave assurance modules wired through one authenticated, closed-world, advisory-only registry.
- Recovered peer-wave catalog/search/saturation surfaces retained as non-authoritative evidence; wave 18 fails closed where its source surface matrix is unavailable.
- AxiomForge OSS integration seams reconstructed as target-native optional integrations rather than donor-shaped competing authorities.

## Validation

- Broad dependency-available Python regression: **767 passed, 11 skipped, 4 subtests passed, 0 failures**.
- Release-manifest acceptance set: **76 passed, 2 skipped, 4 subtests passed, 0 failures**. Both skips explicitly require process-level authentication isolation.
- Recovered target-node accountability: **135/135 target paths present** across **288 recovered ledger rows**.
- Product Python module accountability: **273 modules** hash-accounted.
- Generated OpenAPI: **193 paths**, including authenticated production and late-wave assurance endpoints.
- Late-wave assurance registry: **25/25 reconstructed assurance modules registered**, all advisory-only and mutation-incapable.
- GitHub workflow uses: all pinned to immutable 40-character commit SHAs.
- Shebang/executable permission gate: passed.
- Dependency-free Trust Wallet Node test: passed.

One composite validation command hit its outer 120-second command timeout after printing a transient failure progress marker but no traceback. The immediately isolated full broad rerun completed cleanly at 767 passed / 11 skipped; the marker did not reproduce.

## Validation — 31.7.1 remediation pass (2026-08-24)

- Full dependency-complete Python regression (Python 3.12.14, all optional deps
  installed): **1060 passed, 3 skipped, 4 subtests passed, 0 failures**. The ten
  dependency-gated exclusions recorded in `VALIDATION_RECEIPT.json` no longer apply:
  every previously excluded area now runs in-suite.
- Frontend: Vitest **7/7 passing** via the exact CI command (`npm run test:ci`) and
  production Vite build green (157 modules). Operator workspace capability F-017 is
  therefore **runtime-verified**, upgrading the prior static-only claim.
- New in this pass, all covered by tests: automatic replay-safe mutation retry
  (`trading_gym/production/mutation_retry.py` + coordinator ambiguous-key fail-closed
  gate + durable outbox journal), broker-style `margin_level` metric, pluggable reward
  library (`trading_gym/rl/rewards.py`), and hypothesis-based execution-engine
  invariant oracles.
- Product module accountability regenerated after changes: **296 modules**
  hash-accounted (`docs/contracts/module_accountability.json`).
- P0 defects found and fixed during this pass are enumerated in CHANGELOG `[31.7.1]`,
  notably: env/engine liquidity-cap contract conflict (every env step raised),
  z-scored observation features leaking into execution microstructure math, a pandas-3
  timestamp-resolution bug in poller interval validation, Starlette >=0.46 router
  wrapper breakage in surface tests, and a pytest-9 fixture-mark collection blocker.

---

## Explicit limitations

1. `Trading_Gym_30.0.0_SURFACES.csv` was not recovered. The claimed 13,238 wave-18 surfaces therefore are **not reconstructed or fabricated**; wave-18 saturation stays fail-closed.
2. The validation container runs Python 3.13.5 while the project declares Python `>=3.10,<3.13`. Locked `hypothesis 6.147.0`, `gymnasium 0.29.1`, and `pyarrow 15.0.2` are unavailable in this container, and network installation is blocked. Ten dependency-gated test files therefore were excluded from the dependency-available broad run and are listed in `VALIDATION_RECEIPT.json`.
3. Frontend `node_modules` are absent and network installation is blocked, so the full Vitest/Vite frontend run was not executable locally. The operator workspace capability F-017 is therefore `statically-validated`, not runtime-verified. The dependency-free Trust Wallet Node test passed, and release CI provisions Node 20 and runs `npm ci`, Vitest, and the production build.
4. The original `gm-crypto-rs` donor source bytes were not recovered. Its `third_party` placeholder is deliberately inert and must not be interpreted as recovered executable donor code.
5. No real credential-bearing venue, wallet, chain, payment, deployment, or other external production side effect was exercised.

## Release truth

The authoritative machine-readable reconstruction identity is `trading_gym/release_manifest.json`. `VALIDATION_RECEIPT.json` records the executed proof boundary. `docs/contracts/recovered_target_coverage.json`, `docs/contracts/module_accountability.json`, and `docs/contracts/openapi.json` are deterministic checked artifacts. `SHA256SUMS` binds the final source contents after freeze.
