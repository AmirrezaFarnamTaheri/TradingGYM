# Verified engineering increment — 2026-10-20

## Scope and qualification

This increment starts from upstream `ec3ef39` plus the previously reconciled, uncommitted local changes. The recovery stash was not replayed. No subagents, commits, pushes, deployments, provider purchases, account creation, or live orders were used.

The work implements a schema-publishing repair, fail-closed training-status UX, measured lint cleanup, and an updated responsibility map. It is **not** a whole-project correctness certificate or funded-live qualification.

## Implemented changes

### One schema publisher, consistent defaults

[`apps/api/openapi.py`](../../apps/api/openapi.py) retains FastAPI's route/security generation and metadata, but restores the component definitions produced by its existing Pydantic schema machinery before the pinned FastAPI envelope serialization drops `default: null`. Both the live document and [`scripts/export_openapi.py`](../../scripts/export_openapi.py) use the cached publisher installed on the app. Generated JSON is not patched by hand.

Tests cover nullable defaults versus required-nullable fields, aliases, factory defaults, distinct Decimal input/output representations, request validation, served HTTP schema, cached identity, and preexisting custom schemas. The publisher uses private compatibility helpers from pinned FastAPI: rerun these contracts when upgrading that dependency.

The regenerated snapshot now describes the already-implemented `/api/v1/production/reconciliation` route and the intelligence status `probe_limit` parameter. Publishing a previously omitted route is not adding new trading authority.

### Training status and recovery

[`TrainingPanel.jsx`](../../frontend/src/components/TrainingPanel.jsx) no longer treats an unresolved or failed status read as idle. Unknown/malformed status blocks start; starting/running/stopping block competing starts. Cached PID evidence is hidden after failed status reads. A read-only retry can re-establish status.

Start/stop failures remain visible in the panel instead of relying on browser alert dialogs. Mutations await status revalidation on settlement, including failure, so the start control does not briefly re-enable against pre-mutation idle data. Existing safe model-version validation and bounded/redacted log admission are preserved. These are operator controls, not substitutes for server-side process or financial authorization.

### Behavior-preserving cleanup

Whole-tree Ruff findings reduced from 19 to zero. Changes remove four unused test imports and an unused typing import, expand compact multi-statements, and replace assigned lambdas in tests with named functions. Existing intelligence import names are explicitly preserved and identity-tested rather than silently deleted as supposedly dead public API.

A 64-test characterization set passed before cleanup; its same tests pass afterward. A broader 94-test run reported 93 passes and one existing provider-authentication exception-contract failure also reproduced before cleanup. No fallback or credential-pool algorithm was loosened to make it pass.

### Architecture guide

The [self-contained guide](../../trading-gym-visualization.html) now links the schema publisher, candidate/status UI, and advisory-provider implementations. It distinguishes responsibilities from deployment processes and links this evidence instead of asserting universal readiness. There are still seven responsibility groups; no new framework, language, dependency, or execution service was introduced.

## Measurements

| Observation | Before | After |
|---|---:|---:|
| Whole-tree Ruff findings | 19 | 0 |
| Published API paths | 206 | 207 (existing reconciliation route now included) |
| Explicit null defaults in published document | 13 | 167, generated from model definitions |
| Focused schema/default contracts | 6 failing / 7 passing in the initial reproduction | Entire schema/composition set passes: 28 tests |
| New publisher statement coverage | absent publisher | 100% (15 executable statements) |
| Full frontend test count | 87 passing at integration baseline | 96 passing after this increment |

The cached schema identity was preserved across 1,000 calls; a local diagnostic measured 0.221 ms total. This is not an HTTP latency benchmark or an improvement claim. Cold schema publication performs compatibility-definition work once per cache lifecycle; no cold-start speedup is claimed.

## Verification commands and observed results

The Python commands below used the existing isolated environment, not a dependency-complete Poetry installation.

### Contracts and coverage

```bash
scratchpad/refactor-venv/Scripts/python.exe -m pytest tests/contracts tests/api/test_convergence_composition.py --cov=apps.api.openapi --cov-report=term-missing --cov-report=json:scratchpad/openapi-coverage.json -q --tb=short
```

Observed: **28 passed**, publisher statement coverage **100%**. An earlier intermediate run correctly failed two accountability checks before the manifest was regenerated; those checks were not removed.

### Frontend

```bash
npm run test:ci --prefix frontend
```

Observed: **96 passed** across 19 files, including seven new status/recovery cases and two executable offline-guide checks. The training file itself has 11 passing tests. Browser testing also reproduced and fixed an SVG keyboard-handler defect (`SVGElement` has no `.click()` method); Enter/Space now open/close details via a dispatched mouse event. All six visible guide tabs, open-panel translation, theme selection, component filtering, and label bounds were checked; a 480px viewport had no horizontal page overflow, and the 768px layout was visually inspected.

```bash
npm run build --prefix frontend
```

Observed: production build passed. Existing Vite CJS and React test/deprecation warnings are not presented as errors or hidden.

### Whole Python suite

```bash
scratchpad/refactor-venv/Scripts/python.exe -m pytest tests -q --continue-on-collection-errors --tb=line
```

Observed: **3,001 passed, 33 failed, 16 skipped, 18 errors**, with 140 warnings. This was a whole-suite attempt, not a clean pass. `--continue-on-collection-errors` retains/report errors while allowing other tests to run; it does not turn them into skips.

Failures include Windows symlink privileges, POSIX mode expectations, oversized parameterized environment variables exceeding Windows limits, missing ML/WebSocket/visualization dependencies, and existing execution/intelligence/skill-validation mismatches. Remaining semantic mismatches need investigation; they are not all labeled platform limitations. The full bounded-name failure inventory is below.

### Static and derived artifacts

```bash
ruff check apps trading_gym axiomforge scripts tests
```

Observed: passed after cleanup.

```bash
python -m compileall -q apps trading_gym scripts
```

Observed: passed.

```bash
python scripts/audit_module_accountability.py --check
```

The existing generator was used to refresh the manifest after source cleanup. Final verification is required alongside the source diff; prior historical manifests were not substituted.

```bash
scratchpad/refactor-venv/Scripts/python.exe scripts/export_openapi.py --check
```

Observed: generated live/published contract agrees, including all nullable-default tests.

### Tooling/security limits

The isolated environment has no `mypy` or `pip_audit` module; attempts fail with `No module named mypy` / `No module named pip_audit`. No clean typing or Python CVE-audit result is claimed. `npm audit --offline --json --prefix frontend` reports zero cached findings, but is not fresh advisory-database assurance. No dependency versions or lockfiles were changed to hide these gaps.

Raw local outputs are retained in ignored `scratchpad/`: `openapi-publishing-red.txt`, `openapi-contract-validation.txt`, `openapi-coverage.json`, `training-readiness-red.txt`, `training-post-mutation-red.txt`, `evolution-frontend-tests.txt`, `evolution-frontend-build.txt`, `mechanical-cleanup-before.txt`, `mechanical-cleanup-after.txt`, and `evolution-full-tests.txt`.

## Capability matrix

| Capability | Evidence-qualified status | Evidence / next gate |
|---|---|---|
| Default-preserving live/published API schema | implemented-and-tested | Seven publisher tests plus full contracts; upgrade compatibility helpers under test |
| Candidate submission/status UI | implemented-and-tested with mocked API boundaries | Explicit version, unknown/busy gating, retry, error feedback; actual training requires admitted data and full RL environment |
| Principal-bound approval, runtime barrier, emergency authority | unchanged; local contracts only | Existing tests; complete broker/deployment fault injection still required |
| Execution retry/reconciliation/accounting | simulated / not fully qualified | Full suite retains failures; no financial algorithms changed in this increment |
| Strategy simulation and advisory intelligence | simulation/configuration-dependent | No production model activation or new provider support claimed; existing intelligence baseline failures remain |
| Multi-venue funded failover / multiple account writers | unsupported as qualified behavior | Requires independent capital ownership, fencing and broker-actual reconciliation evidence |
| Cross-platform and dependency-complete suite | unverified | Symlink-capable Windows/POSIX CI and complete locked environment required |
| Offline architecture guide | documentation, not authority | Source-linked responsibilities and explicit qualification limits |

## Deferred work and rationale

- Do not unify reconciliation/audit methods solely because they look similar: locking, ownership, and domain failures differ.
- Do not restore unfinished profile-discovery work from the recovery stash or invent support from provider presets.
- Do not broaden automatic venue failover, add language/toolchains, or weaken risk/credential gates to increase apparent capability.
- Restore complete locked development tooling and investigate the remaining semantic regressions before claiming full source qualification. Prove real broker outcomes, restart/fencing behavior, and independent approval in the target environment before funded use.

## Remaining full-suite failures and collection errors

Parameterized data is deliberately omitted from identifiers below to keep oversized fixture bodies and credential-like test data out of documentation. Raw diagnostics remain in the ignored local log.
- **ERROR** `tests/adapters/test_websocket.py`
- **ERROR** `tests/apps/test_trade_loop.py`
- **ERROR** `tests/experiments/test_no_trade_prune.py`
- **ERROR** `tests/integration/test_system_sanity.py`
- **ERROR** `tests/intelligence/test_llm_http_envelope.py::test_successful_http_with_invalid_evidence_is_terminal_not_provider_outage` (2 cases)
- **ERROR** `tests/pipelines/test_env_creation.py`
- **ERROR** `tests/pipelines/test_evaluation.py`
- **ERROR** `tests/rl/test_borrow_cap_wrapper.py`
- **ERROR** `tests/rl/test_borrow_cap_wrapper_comprehensive.py`
- **ERROR** `tests/rl/test_environment_logic.py`
- **ERROR** `tests/rl/test_margin_stop_out_env.py`
- **ERROR** `tests/rl/test_normalized_env.py`
- **ERROR** `tests/rl/test_normalized_env_type_contract.py`
- **ERROR** `tests/rl/test_rewards.py`
- **ERROR** `tests/rl/test_rl_absorptions.py`
- **ERROR** `tests/rl/test_z7_env_regressions.py`
- **ERROR** `tests/visualization/test_training_plots.py`
- **FAILED** `tests/api/test_api_supervisor_lock.py::test_lifespan_rejects_symlink_runtime_root`
- **FAILED** `tests/common/test_persistence_toctou.py::test_read_json_rejects_symlinks_with_the_lock_held`
- **FAILED** `tests/convergence/test_backtest_authority_boundary.py::test_backtest_rejects_symlinked_output_root`
- **FAILED** `tests/convergence/test_config_snapshot_hardening.py::test_master_config_snapshot_rejects_symlink_directory`
- **FAILED** `tests/convergence/test_data_loader_admission.py::test_loader_rejects_path_traversal_and_symlinked_members`
- **FAILED** `tests/convergence/test_fifteenth_pass_production_runtime.py::test_live_adapter_delegates_mutation_to_single_production_coordinator`
- **FAILED** `tests/convergence/test_lake_integrity_hardening.py::test_lake_manifest_rejects_symlinked_artifacts`
- **FAILED** `tests/convergence/test_model_snapshot_boundary.py::test_verified_snapshot_rejects_hash_mismatch_symlink_and_oversize`
- **FAILED** `tests/convergence/test_reproducibility_manifests.py::test_source_tree_identity_is_deterministic_and_rejects_symlinks`
- **FAILED** `tests/convergence/test_second_pass_hardening.py::test_atomic_json_rejects_symlink_targets`
- **FAILED** `tests/convergence/test_second_pass_hardening.py::test_confined_child_rejects_traversal_and_symlink`
- **FAILED** `tests/convergence/test_second_pass_hardening.py::test_panic_signal_persists_until_flatten_succeeds`
- **FAILED** `tests/convergence/test_second_pass_hardening.py::test_runtime_control_files_are_atomic_and_retryable`
- **FAILED** `tests/convergence/test_second_pass_hardening.py::test_symlink_panic_is_fail_closed_and_not_cleared`
- **FAILED** `tests/convergence/test_secret_rotation_script.py::test_symlink_and_duplicate_definitions_fail_closed`
- **FAILED** `tests/execution/test_live_execution_adapter.py::test_live_equity_rejects_invalid_balance_components`
- **FAILED** `tests/intelligence/test_dynamic_resilience.py::test_full_capability_degrades_to_llm_and_restores`
- **FAILED** `tests/intelligence/test_dynamic_resilience.py::test_model_free_auto_recovers_llm_and_uses_rules_when_down`
- **FAILED** `tests/intelligence/test_llm_fail_closed_rotation.py::test_exhausted_authentication_is_not_a_recoverable_model_outage`
- **FAILED** `tests/intelligence/test_omni_intelligence.py::test_llm_health_probe_exception_isolated_without_secret_leak`
- **FAILED** `tests/intelligence/test_request_budget.py::test_attempts_share_one_state_and_count_failures`
- **FAILED** `tests/intelligence/test_request_budget.py::test_corrupt_state_denies_instead_of_reset`
- **FAILED** `tests/intelligence/test_system1_rotation.py::test_system1_context_rejects_nonphysical_or_nonfinite_return_statistics`
- **FAILED** `tests/monitoring/test_health.py::test_model_probe_requires_regular_nonsymlink_file`
- **FAILED** `tests/platform/test_live_admission_runtime.py::test_model_free_system1_runs_only_in_non_mutating_live_dry_run`
- **FAILED** `tests/platform/test_live_admission_runtime.py::test_unqualified_system1_never_enters_funded_live_execution`
- **FAILED** `tests/platform/test_source_release.py::test_source_release_requires_git_index`
- **FAILED** `tests/skills/test_archive_intake.py::test_archive_symlink_to_outside_denied`
- **FAILED** `tests/skills/test_archive_intake.py::test_malicious_member_path_denied`
- **FAILED** `tests/skills/test_skill_capability_preview_cli.py::test_symlink_input_is_rejected`
- **FAILED** `tests/skills/test_skill_security_hardening.py::test_intake_refuses_symlink_and_oversized_data`
- **FAILED** `tests/skills/test_skill_security_hardening.py::test_quarantine_reviewer_hash_uniqueness_and_defensive_copies`
- **FAILED** `tests/tooling/test_source_impact.py::test_no_symlink_escape`
