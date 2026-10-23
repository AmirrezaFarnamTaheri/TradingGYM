# Upstream onboarding and local integration

> Integration-time evidence below is historical. The subsequent [verified engineering increment](EVOLUTION_PASS.md) repairs schema publication and the recorded whole-tree Ruff findings, adds fail-closed training-status UX, and records the latest broader test outcomes. Earlier failure counts are not current-state claims.

## Synchronization

- Original checkout: `main` at `1ff5890`.
- Fetched `origin/main`: `ec3ef39` (`Merge pull request #2 from AmirrezaFarnamTaheri/audit/2026-10-11-execution-ci-hardening`).
- Divergence before sync: zero local-only commits, 866 upstream-only commits.
- Fast-forwarded the ordinary repository checkout; no merge commit, new implementation commit, push, or deployment was made.
- Recovery stash retained: `de9fdc1cd80bab41867d87b3e30fe846d1cd07c2`, labeled `pre-upstream-sync local evolution and simplification` (`stash@{0}` at integration time). It contains tracked product edits and untracked product additions. Existing local `.claude/`, `.gemini-tasks/`, and `.claudeignore` were excluded from stashing and left in place.
- An additional tracked-change patch is retained at ignored `scratchpad/pre-upstream-sync.patch`; untracked files are preserved by the stash, not that patch.

Do not blindly apply the entire stash: it contains superseded implementations, unfinished work, and a local `TrainingPanel.test.jsx` that collides with a newer upstream file.

## Upstream understanding

The 317-file upstream diff materially changes the project, not just its version label. Reviewed boundaries include:

- One API supervisor per data home, cancellation-safe managed-process cleanup, selected-provider credential delegation, and RL-only admission for funded live operation.
- Fail-closed unknown-order outcomes, pagination completeness, gross-exposure and account-wide emergency reconciliation; no declaration of safe cross-host or multi-writer funded execution.
- Bounded/coalesced market-data reads, generation-fenced circuit recovery, native System-One and multi-key provider research, and explicit advisory/authority separation.
- Replay, paired research, point-in-time fundamentals/universe evidence, numerical strategy integrity, funding, settlement, and single-account negative-cash safeguards.
- Broader production authority/reconciliation views, corrected training candidate submission, sanitized log telemetry, in-memory browser keys, and stale-evidence fencing.
- Single-pass coverage execution and cancellation of superseded CI runs.

The current [resilience operating contract](RESILIENCE-OPERATING-CONTRACT.md) remains authoritative about unsupported multi-host fencing, actual broker settlement, and live-qualification obligations. Native/advisory provider integration is not evidence of funded trading permission.

## Reconciliation decisions

| Local work | Decision |
|---|---|
| Convergence request-schema and evaluation extraction | Retained. Exact comparison against a freshly constructed upstream router yields identical OpenAPI for 134 paths and 312 schemas; original request-model imports remain available. |
| Shared directory-sync implementation | Retained in poller/feature pipeline, reusing the current upstream persistence helper. |
| Cross-platform developer task runner and wrappers | Retained. CI typecheck delegates to the same target list; upstream CI concurrency and single-pass coverage changes remain intact. |
| Actor-header construction and boundary tests | Retained without replacing upstream training/production client changes. |
| Session-only browser authentication | Use upstream implementation. Retain local negative tests as additional coverage. |
| Explicit candidate version and installed training default | Use upstream `TrainingPanel`, API, telemetry normalization, and tests. Do not overwrite with the unfinished local component/test design. |
| Experimental training-profile discovery API/module/tests | Left in recovery stash; not automatically integrated as a new feature during upstream sync. |
| Grant-clock tests and revision-bound README tests | Use expanded upstream tests instead of the older local replacements. |
| Poller tests | Retain constructor-based temporary storage isolation and remove redundant mocking/commentary. |
| Old project/capability/findings/baseline inventories | Preserve in stash rather than publish outdated pre-upstream inventories as current. |
| Simplification evidence | Retain as explicitly historical; this record supersedes its current-state claims. |
| Module accountability | Regenerated from current source; verifies 365 modules. |
| OpenAPI snapshot | Preserve upstream annotations. Exporter comparison exposes existing drift, including missing reconciliation route/probe parameters; naive regeneration additionally removes nullable defaults required by upstream tests. Resolve that publisher inconsistency separately rather than silently weakening schema tests. |

## Small upstream blockers corrected during integration

These defects were reproduced on the fetched sources, not introduced by local replay:

1. `IntelligencePanel` imports a named `api`, while upstream only exported the default client. Added a named alias to the same authenticated singleton, preserving default callers and credential handling. Added a failing-then-passing API-export regression test.
2. `ModelsPanel` reads undeclared `activeIsError` / `activeError` variables. Bound the query error and consistently used the existing `activeStatusError` flag. Upstream model-state tests reproduced the render crash and now pass.
3. Intelligence freshness tests shared a mock without resetting its call history: an expectation of two calls observed nine across tests. Reset the mock between cases, preserving the exact call-count assertion.
4. The gateway-mount test still expected the old health response despite upstream adding `legacy_high_risk_wildcard_grants`. Updated the exact expected response to include zero historical broad grants; no production behavior changed.

## Verification

- `npm run test:ci --prefix frontend`: **87 passed** across 18 test files.
- `npm run build --prefix frontend`: **passed**.
- Current convergence router compared with `git show HEAD:apps/api/convergence.py` instantiated separately in the same environment: **exact parity, 134 paths / 312 schemas**.
- `python scripts/audit_module_accountability.py --check`: **365 modules verified**.
- `python -m compileall -q apps trading_gym scripts`: **passed**.
- `git diff --check`: **passed**.
- Whole-tree Ruff reports **19 pre-existing findings** in upstream intelligence and research test/source files. No broad unrelated reformatting or import deletion was applied.
- The first post-sync targeted Python matrix reported **315 passed, 5 failed**: Windows symlink privilege, stale auth-health assertion, OpenAPI snapshot drift, and two Gymnasium-dependent live-admission tests. The stale assertion was corrected; missing dependencies and OS privileges were not bypassed.
- Final targeted Python matrix: **314 passed, 2 failed**. Remaining failures are `tests/api/test_api_supervisor_lock.py::test_lifespan_rejects_symlink_runtime_root` (`WinError 1314`) and `tests/contracts/test_openapi_contract.py::test_committed_openapi_matches_the_live_app` (the upstream publisher/snapshot inconsistency described above). Neither was suppressed.
- Changed Python files: **Ruff passed**; index has no staged changes, no merge conflicts remain, and `HEAD...origin/main` reports **0 ahead / 0 behind**.
- Full dependency-complete CI and live venue qualification are not claimed.

Local raw outputs are retained under ignored `scratchpad/post-upstream-*.txt`. Python commands use `scratchpad/refactor-venv/Scripts/python.exe`; that environment is not a full Poetry installation.
