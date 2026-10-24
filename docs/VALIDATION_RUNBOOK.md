# Revision-bound validation

Use `python scripts/validation_receipt.py --source-sha <40-character-sha> --output <receipt.json> --scope <precise-path-or-suite> -- <test-command>` to record an actual command's exit code, environment, source/index/worktree identity and output digest. Preserve ordinary CI logs separately. Do not put secrets in test command arguments. A named command passing does not qualify other paths, profitability or a venue integration.

To diagnose Actions, export the read-only jobs response for the exact run using GitHub's Actions jobs API, including every page, and pass it with `--jobs-json <export.json>` instead of a command. The classifier verifies the complete required job-name inventory, all four Python/OS matrix cells, uniqueness, and that every executed success reached `completed`. All job records must also belong to the same positive GitHub `run_id`; jobs from separate workflow runs on the same SHA cannot be merged into an apparent success. GitHub jobs from different `run_attempt` values of one run remain valid after a partial rerun. Missing pages, duplicate jobs, or newly introduced unrecognized jobs fail closed and are reported explicitly; when CI is expanded, update `REQUIRED_CI_JOBS` and the corresponding inventory test together. A completed job with no steps and no assigned runner has no execution evidence; it is not a unit-test failure. Missing or mismatched SHAs fail admission.

**External step:** inspect that run's job annotation and the repository/account's Actions status. Establish the actual admission cause, have the authorized administrator resolve it, then retain a started current-revision run and its logs. YAML edits or this receipt cannot resolve billing, organization policy or runner capacity. No automated rerun or account change is performed by the diagnostic tool.

Generate current documentation from the scoped receipts. Historical counts remain historical; failed, skipped and not-run capabilities must remain visible. Official reference: https://docs.github.com/en/rest/actions/workflow-jobs .

A pull-request CI run intentionally skips the job named `Dependency Scan (weekly, informational)` because `.github/workflows/ci.yml` runs it only on weekly schedules. The receipt classifies this *one exact completed skip* as `intentional_non_gating_skip`; it does not count as executed, and it cannot alone qualify a run. A skipped required job (such as Integration Tests), an incomplete/pending scan, or a failing scan still blocks `all_executed_successfully`. The caller must export **all job pages** and review each required job; passing the supplied jobs alone is not evidence that an omitted job ran.

## Exact revision admission

The receipt helper now fails closed if the supplied SHA differs from Git HEAD,
Git is unavailable, the source root differs, the index or worktree is dirty,
or the named command changes that source during execution. Keep disposable
untracked logs and exported job metadata outside the checkout when generating
an official revision-bound receipt. Existing historical receipts are not
retroactively granted these stricter guarantees.

Output paths inside the repository are rejected before the named command runs, including paths reached through external symlinks. This prevents a successful validation receipt from modifying or overwriting its own certified checkout. Use an external, access-controlled directory for JSON receipts and job metadata.
