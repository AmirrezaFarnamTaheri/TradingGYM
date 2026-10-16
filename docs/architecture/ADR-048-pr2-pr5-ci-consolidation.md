# ADR-048 — Consolidate PR #5 single-pass CI into PR #2

**Decision (2026-10-15):** Keep PR #2 as the only open product implementation
PR. Integrate PR #5's unique CI improvement without merging either PR into
`main` or changing existing safety/release gates.

## Source review and provenance

- PR #2: `audit/2026-10-11-execution-ci-hardening` — the large draft
  trading-safety, research, frontend and provider convergence branch.
- PR #5: `ci/tradinggym-single-pass-coverage` — one independent commit
  versus PR #2's then-current head, changing only
  `.github/workflows/ci.yml`; all other files were common ancestry.
- Both PRs showed failed required Actions jobs with **zero recorded
  execution steps and no runner assignment** on their sampled October 15
  workflow runs. These are infrastructure/runner-allocation failures,
  **not evidence** that source checks passed or failed.

## Behavior preserved, portability defect corrected

The original test matrix contains Ubuntu and Windows runners on Python
3.11 and 3.12. The previous workflow ran all non-integration unit tests
once in every matrix cell and **twice** on Ubuntu Python 3.12, where it
also collected coverage.

PR #5 attempted to fold these into one inline Bash `[[...]]` conditional,
but that script was inside a multi-OS `run` step. GitHub's default
Windows runner shell is PowerShell; relying on Bash syntax in an
unqualified step would break Windows test execution.

The consolidated workflow uses two **mutually exclusive GitHub Actions
step `if` conditions**, not shell logic:

| Runner | Plain unit step | Coverage unit step |
| --- | --- | --- |
| Ubuntu / Python 3.11 | yes | no |
| Ubuntu / Python 3.12 | no | yes |
| Windows / Python 3.11 | yes | no |
| Windows / Python 3.12 | yes | no |

The covered cell retains `--cov=trading_gym --cov=apps`, XML/terminal
reports, and a Codecov upload conditional on the **same cell**. All cells
still run `pytest -m "not integration"`; no tests are skipped to
mask errors. Scheduled dependency scans and existing integration,
security, Rust, frontend and type-checking jobs are untouched.

## Tests and incomplete verification

`tests/platform/test_ci_single_pass.py` statically parses the YAML
using the project's PyYAML dependency and checks:

- The expected Ubuntu/Windows and Python 3.11/3.12 matrix.
- Exactly one plain and one coverage-producing test step,
  mutually exclusive for every matrix cell.
- No Bash- or PowerShell-specific logic in these cross-platform steps.
- The unchanged XML coverage upload and its matching condition.

An independent Python 3.13/PyYAML 6.0.3 isolated matrix check parsed the
equivalent step block and validated all four cells. A full pinned
repository checkout and `actionlint` are unavailable in this
environment, and **no green PR-head Actions job has been recorded**.
Billing/runner allocation must be restored independently; modifying
CI YAML cannot create unavailable GitHub-hosted runners.

## Branch convergence and release boundary

Integrate using an ancestry-preserving merge commit whose first
parent contains PR #2's latest source plus the reviewed CI fix, and
whose second parent is PR #5's head. Retire PR #5 only after verifying
the PR #5 commit is an ancestor of the consolidated PR #2 head.
This operation updates no `main` ref.

PR #2 remains **draft**, and funded-live trading remains **NO-GO**
until exact-head Python/frontend/Rust/security and broker settlement/
emergency-recovery gates have real passing evidence.
