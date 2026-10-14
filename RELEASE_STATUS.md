# Hardening branch status — 2026-10-11


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
