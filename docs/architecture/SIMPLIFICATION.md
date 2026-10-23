# Simplification evidence — 2026-10-20

> Historical pre-sync evidence: these measurements and results were captured on the working tree based at `1ff5890`, before integrating 866 upstream commits. They are not current validation claims. Some local fixes were superseded by upstream; see [the integration record](UPSTREAM_SYNC.md) for the retained changes and post-sync results.

## Environment

Windows 11, Python 3.11.9, with an isolated test environment under the ignored `scratchpad/` directory. FastAPI, Pydantic, Starlette, HTTPX, and pytest were subsequently aligned with `poetry.lock` for contract checks. This environment does not contain the full training/visualization dependency stack and cannot create Windows symbolic links (`WinError 1314`). It is not a production-qualification environment.

## Changes and measurements

| Area | Before | After | Reason |
| --- | --- | --- | --- |
| Convergence gateway | 2,790 lines | 2,166 lines | Separate request contracts and read-only computations from stateful service composition. |
| Convergence factory | 2,172 lines | 1,973 lines | Move 40 closure-free handlers without changing their bodies or request annotations; retain the static template route ahead of the dynamic strategy route. |
| Request contracts | 45 classes embedded in gateway | One schema module, re-exported by the original module | Preserve imports and validation while reducing route-module responsibilities. |
| Directory synchronization | Three identical implementations | One implementation, reused at all three sites | Keep platform durability behavior in `trading_gym.common.persistence`. |
| Poller | 360 lines | 339 lines | Remove the duplicated synchronization helper. |
| Feature pipeline | 434 lines | 417 lines | Remove the duplicated synchronization helper. |
| Browser actor headers | Ten repeated header literals | One fresh-object helper | Preserve endpoint payloads, timeouts, and actor separation. |
| Developer commands | Module command names repeated in parser choices and dispatch | Parser choices derive from one module-command registry | Avoid drift and rebuilding static command definitions per invocation. |
| Grant tests | Real wall clock mixed with a fixed issuance date | Explicit fixture clock for non-clock tests | Keep scope/constraint assertions independent of calendar date; the default-clock expiry test remains intact. |
| Poller test setup | Mutate `data_dir` after construction while manifests still point at the repository | Supply both temporary directories through the constructor | Keep regression tests from rewriting tracked lake metadata. |
| Documentation checks | Launch another test collection and assume collected means passed | Link to revision- and environment-specific evidence | Remove misleading pass-count promises and collection subprocess overhead. |

This is a structural simplification, not a rewrite of the trading, accounting, approval, or risk algorithms. Large stateful modules remain: file-size reduction alone does not justify introducing artificial boundaries or shared base classes.

## Compatibility and authority

- All 134 convergence OpenAPI paths and 312 component schemas matched exactly before and after extraction in the same library environment.
- The extraction script compared the executable AST of every moved request class and evaluation function before writing the split modules.
- Original request-model imports remain available from `apps.api.convergence`.
- New composition tests exercise gateway rejection across every extracted evaluation route.
- Read-only evaluation functions do not capture repository/service instances.
- No task generates trading credentials, infers an admitted model, grants approval, or bypasses the live runtime barrier.
- No parallel repository-reconciliation mixin was introduced: similar methods have different domain error and locking contracts.

## Validation

Results below are observations, not an assertion that the whole project is fully green.

- Pre-extraction API/authority baseline: **8 passed**.
- Same-environment OpenAPI comparison: **exact parity** for 134 paths / 312 schemas.
- Broad API, convergence, accounting, treasury, risk, polling, feature, and tooling matrix: **560 passed, 1 skipped, 10 failed**. All ten failures occurred while test fixtures attempted Windows symlink creation, with `WinError 1314`.
- Full discoverable-suite attempt before the follow-up clock/documentation fixes: **1,574 passed, 6 skipped, 22 failed, 16 collection errors**, plus 8 passing subtests. Missing packages included Gymnasium, Optuna, WebSockets, and Matplotlib; symlink privileges, calendar-sensitive grant tests, documentation checks, and installed-library schema drift also contributed. This was not a clean full-suite run.
- Follow-up deterministic grant suite: **20 passed**; production grant evaluation code was not changed.
- Final focused matrix (composition, route precedence, committed OpenAPI, documentation, accountability, durability, grants, polling, features, and tooling): **63 passed** under the lockfile-aligned API/test libraries.
- Frontend after header consolidation: **41 tests passed**, and the production Vite build succeeded.
- Ruff: all product/application/script/test trees checked successfully.
- Compilation: application, product, and script Python sources compiled successfully.
- Module-accountability manifest regenerated and verified for **337 modules**.

Raw local test outputs are retained under the ignored `scratchpad/` directory. Dependency-complete CI, symbolic-link-capable security tests, and a built-kernel parity run remain necessary; none were waived or represented as passed.

## Navigation

- [Request contracts](../../apps/api/convergence_models.py)
- [Read-only evaluation routes](../../apps/api/convergence_evaluations.py)
- [Stateful convergence composition](../../apps/api/convergence.py)
- [Cross-platform developer tasks](../../scripts/project.py)
- [Offline interactive architecture guide](../../trading-gym-visualization.html)

The visual guide describes seven responsibility groups, not seven independently deployed services. It includes source links, English/Chinese text, theme selection, keyboard interaction, and an intentionally hidden relational-schema view: local file stores are not presented as a relational database.
