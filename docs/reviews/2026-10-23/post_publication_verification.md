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
