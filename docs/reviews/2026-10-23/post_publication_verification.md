# PR #6 — post-publication verification and blockers

**Date:** 23 October 2026. **Head reviewed:** `7b9d60aea93c46a4f43be52438458c48384bb8b5`. **Branch:** `fix/audit-integration-2026-10-23`.

This is an additive record. The original [validation.json](validation.json)
remains evidence of the earlier frozen implementation commit
`865beaf83ae067923aa293c1fc8b17b2f0fb8400`, **not** a whole-suite pass
for the current PR head. The existing 3,461-pass complete Python result,
396-pass independent contracts, 113-pass frontend suite and production build
must not be silently promoted to a different source revision.

## Delivered follow-ups

| Commit | Change | Regression evidence |
| --- | --- | --- |
| `ac974b5f0f78fd17bb627b0aa567017065f31c85` | Reject mismatched HEAD, dirty index/worktree, untracked source and source changes during release validation | New `tests/scripts/test_validation_revision_binding.py`: seven isolated real-Git checks |
| `a39fb009b3debefea804e57a35692365daa1eca4` | Refuse to archive staged/unstaged tracked mutations and discard an archive on a late detected change | New `tests/scripts/test_source_archive_integrity.py`: four isolated real-Git checks |
| `6d00c254d7f333cc809b0d3d490db520a0b0c7b8` | Installed-container smoke checks built JS/CSS and rejects unprotected/malformed status | New `tests/scripts/test_smoke_installed_service.py`: four local HTTP integration checks |
| `81fb6130fdc102975dc87eac62a918ea5ec53ddb` | Formatting-only follow-up | No behavior change intended |
| `7b9d60aea93c46a4f43be52438458c48384bb8b5` | Limit automated reviewer selection to critical changed application/API/UI/CI paths | `.coderabbit.yaml` selects 149 paths at this revision; external review result not yet verified |

The focused red/green exercise used isolated replicas of the modified
standard-library scripts in a disposable workspace. **18 local tests passed**
in that combined fixture run. The current container interpreter was Python
3.13.5, **outside the application's supported >=3.11,<3.13 range**.
The 18 includes inherited test-method duplication in the local harness; the
15 new tests committed above are distinct. This is evidence for the bounded
fixes, **not** a freshly run full checkout, exact Poetry lock, Rust parity,
Windows, Docker image, or interactive-browser qualification.

Suggested supported-runtime verification on the complete PR checkout:

```bash
python -m pytest tests/scripts/test_validation_receipt.py \
    tests/scripts/test_validation_revision_binding.py \
    tests/scripts/test_source_archive_integrity.py \
    tests/scripts/test_smoke_installed_service.py -v
python scripts/run_contract_tests.py
python -m pytest -v --tb=short
npm --prefix frontend run test:ci
npm --prefix frontend run build
```

Do not declare release-ready until the documented full and native/hosted gates
execute on the current commit.

## GitHub Actions startup blocker

[CI run #587](https://github.com/AmirrezaFarnamTaheri/TradingGYM/actions/runs/40014699667)
on commit `6d00c25` and the preceding runs repeatedly reported **11 failed
and 2 skipped jobs**, with no assigned runners and zero executed steps in all
jobs. [CI #589](https://github.com/AmirrezaFarnamTaheri/TradingGYM/actions/runs/40015187710)
on `7b9d60a` also reported a failed workflow; its job-level startup reason is
not confirmed here. These records do not implicate an actual Python, Node,
Rust, security-linter, or frontend-test command. Never change tests merely to
silence these pre-execution conclusions.

**Required external diagnostic:** Open the failed job's check summary in the
authenticated GitHub browser and inspect the top-level startup annotation.
Check the repository Actions settings, account billing/usage/budgets and hosted
runner eligibility; if there is an administrative restriction, the authorized
owner must resolve it. GitHub lists billing as *one possible class* of workflow
startup failures, but the specific cause for TradingGYM is **not verified**.
Obtain a current-revision run with assigned runner, steps and retained logs.

## Review gate

CodeRabbit skipped the initial PR because it contained more than 150
reviewable files on the available plan. The optional scoped configuration is
a way to request review of critical production code without falsely claiming
that all changed files were reviewed. It does **not** certify tests, generated
contracts, scripts or documentation, and no bot completion is claimed.
Qodo separately reported its inactive review subscription; that is a
provider/account state, not a repository code failure.

## Still unqualified

All-writer application snapshot consistency and isolated accounting recovery;
installed interactive keyboard/browser testing; fresh-model-to-persistent-paper
end-to-end admission; exact-lock Linux/Windows, compiled native-kernel parity,
build-and-run container smoke; real venue/provider costs, order outcomes and
economic performance; capacity, storage retention and long-running restart
qualifications. A successful synthetic fixture does not replace those tests.

## Isolated runner-admission experiment: completed

On **23 October 2026**, commit
[`df1b6d9`](https://github.com/AmirrezaFarnamTaheri/TradingGYM/commit/df1b6d9e687fce0fc7ff415991973df1b44d967f)
temporarily introduced a minimal
`runner-admission-probe.yml` workflow triggered on the PR branch.
Neither probe job checked out the repository, downloaded dependencies,
invoked actions, or used any application code.

[Runner-admission run #40017711750](https://github.com/AmirrezaFarnamTaheri/TradingGYM/actions/runs/40017711750)
completed with **failure** in both bare jobs:

| Bare runner | Job ID | Outcome |
|---|---:|---|
| `ubuntu-latest` | 120633959071 | failed before a runner or step was recorded |
| `windows-latest` | 120633958679 | failed before a runner or step was recorded |

The workflow was removed after the experiment to avoid shipping an extra
per-push workflow. The run and commit remain durable diagnostic evidence.

The connector confirmed that **TradingGYM is a private repository**, whereas
other owner repositories checked at that time—OpenHuman, HUNTX, Scriptor,
and Collegeum—were public and had successfully executed Actions jobs on
23 October 2026. None of the sampled *other private* repositories had
recent workflow runs, so these results do not isolate the problem to this
one repository versus private-repository Actions entitlement in general.

**Interpretation:** The same pre-step failure on Linux and Windows in a
workflow without checkout or any application step is strong evidence against
a Python/Node/Rust test command, action version or heavy-build-disk problem
causing the observed failures. It is consistent with a hosted-runner
provisioning/admission restriction. **A particular cause is not proven.**
Private-repository minutes, spending budget, unpaid billing, runner
eligibility/restrictions, and GitHub service provisioning remain live
possibilities. Do not change repository visibility or waive CI checks as
a workaround without an explicit owner decision.

**Owner/administrative next action:**

1. Open the bare [runner probe run](https://github.com/AmirrezaFarnamTaheri/TradingGYM/actions/runs/40017711750);
   inspect each job's top-level annotation for the actual rejection.
2. Inspect GitHub account **Billing and licensing > Budgets and alerts**
   and Actions usage/spend controls for *private* repositories.
3. Check this repository's **Settings > Actions > General** and runner
   permissions/eligibility.
4. When the cause is remedied, rerun the latest required CI and collect
   assigned runners, executed steps, logs and verified source revision.
   If no annotation explains the outcome, submit the two failing job IDs,
   run ID and UTC timestamps to GitHub Support.

The visible connector can inspect Actions runs and jobs, but cannot read
this account's billing settings or the check-run startup annotations.

## Source-release fixture and formatting follow-up (2026-10-23)

A subsequent manual audit found that `tests/platform/test_source_release.py`
created *staged-only* disposable repositories. Since the stricter source
archiver correctly rejects dirty Git checkouts, three existing tests were
failing before their intended security assertions. Commit
[`30661a6`](https://github.com/AmirrezaFarnamTaheri/TradingGYM/commit/30661a6b6c2d793b5021c1d5cc1c6f1eb10546b5)
creates an actual fixture commit for the baseline repository and commits
unsafe tracked symlink/dotenv fixtures as needed. This retains the fail-closed
archive behavior; it does not relax dirty-tree admission. The commit also
normalizes formatting in the jobs-receipt classifier and its tests.

A local disposable subset at `/mnt/data/tradinggym_followup` ran:

```text
python -m pytest -q tests/platform/test_source_release.py \\
  tests/test_source_archive_integrity.py \\
  tests/test_validation_receipt_integrity.py \\
  tests/test_smoke_installed_service.py
..................... [100%]
21 passed in 7.72s
```

**Scope limitation:** That disposable harness contains isolated copies and
focused analogues, not the entire checked-out current GitHub tree; local
Python was 3.13.5, outside this project's supported 3.11–3.12 range. This
is positive targeted regression evidence, **not** a current-head supported-
runtime or complete-suite qualification. Python compilation for the same
local subset succeeded.

[CI #40019981549](https://github.com/AmirrezaFarnamTaheri/TradingGYM/actions/runs/40019981549)
on `30661a6` still terminated without any assigned runner or executed job
step (**11 failed, 2 skipped**); the first failing job's REST metadata showed
`runner_id=0`, `runner_name=""`, `steps=[]` and an approximately 3-second
lifetime. This does **not** establish a defect in the named test command.

The external runner-admission blocker is tracked in
[P0 issue #7](https://github.com/AmirrezaFarnamTaheri/TradingGYM/issues/7).
An administrator must establish the exact startup rejection and resolve it
before the PR can receive its actual required full CI gate. In particular,
private-repository Actions billing/budget settings require authorized account
access that this connected GitHub tool cannot inspect or modify. The PR is
still open and **not release-qualified**.

## Source ZIP metadata reproducibility hardening (2026-10-24)

Commit [`0cc44b4`](https://github.com/AmirrezaFarnamTaheri/TradingGYM/commit/0cc44b435fa829b28cf648aff2fee53acd258256)
fixes a reproducibility failure: before the change, touching a tracked
file (without modifying its content or Git commit) changed the source
archive's SHA-256, and a clean checkout that disabled Git filesystem
mode tracking could drop the executable bit from an indexed script.

The archive builder now obtains modes from `git ls-files --stage -z`,
rejects symlink entries, uses deterministic ZIP member timestamps,
preserves Git executable bits, and streams source contents. Different
zlib implementations may still produce different compressed bytes.
The fixture explicitly forces executable index mode on Windows using
`git update-index --chmod=+x`.

**Red/green evidence:** Two isolated tests reproduced the bugs before
the fix and passed afterward; nine focused source-archive/release
checks passed in the local fixture workspace, including the existing
symlink and dotenv exclusions. Separately, 18 inherited script-level
unittest checks passed (these results are not a full Python 3.11/3.12
Poetry-locked repository suite). Required CI continues failing before
runner assignment; see [issue #7](https://github.com/AmirrezaFarnamTaheri/TradingGYM/issues/7).

## Immutable-object release archive hardening

Commit [c2c4de3](https://github.com/AmirrezaFarnamTaheri/TradingGYM/commit/c2c4de30ab32191fcee5b6d7ad4578672feb1b6e)
fixes a release-integrity race that survives both worktree cleanliness checks:
the former archive writer read files from mutable filesystem paths, so a
short-lived file substitution could affect ZIP contents and be restored
before the final Git status check. The revised writer takes each entry's blob
identity from the verified Git index, streams it with git cat-file blob,
derives the release root version from the committed manifest, and atomically
replaces the final ZIP only after all reads and a final clean-revision check.
It also rejects path-unsafe manifest versions and retains the previous good
archive when an object read fails.

Commit [5510423](https://github.com/AmirrezaFarnamTaheri/TradingGYM/commit/55104237e185b64e6f72bf950bb33895ad537df1)
preserves the existing error-message contract when the Git checkout is
missing, with an additional targeted regression.

Focused verification used a disposable real-Git fixture and local Python
3.13.5. The tests demonstrated a red-before/green-after cycle for the
transient worktree read and spoofed manifest versions, invalid path versions,
and a missing-Git error. A subsequent local run of
test_source_archive_integrity.py and
test_source_archive_reproducibility.py reported **12 passed, 0 failures**,
including reproducible ZIP bytes, committed executable permissions,
staged/unstaged source rejection, and temporary output cleanup. These are
fixture-scoped checks, **not** the full checkout, exact-lock Python 3.11/3.12,
Windows, built container or Rust native parity.

The latest observed [required CI run #40031336699](https://github.com/AmirrezaFarnamTaheri/TradingGYM/actions/runs/40031336699)
on commit 55104237e185b64e6f72bf950bb33895ad537df1
again completed with **11 failed, 2 skipped**, **zero assigned runners and
zero executed steps**. GitHub runner admission remains an external blocker.
See [P0 issue #7](https://github.com/AmirrezaFarnamTaheri/TradingGYM/issues/7).
Do **not** treat the archive-focused test evidence or CodeRabbit's scoped
walkthrough as substitute qualification for merge or release.
