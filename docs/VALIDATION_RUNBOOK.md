# Revision-bound validation

Use `python scripts/validation_receipt.py --source-sha <40-character-sha> --output <receipt.json> --scope <precise-path-or-suite> -- <test-command>` to record an actual command's exit code, environment, source/index/worktree identity and output digest. Preserve ordinary CI logs separately. Do not put secrets in test command arguments. A named command passing does not qualify other paths, profitability or a venue integration.

To diagnose Actions, export the read-only jobs response for the exact run using GitHub's Actions jobs API, including every page, and pass it with `--jobs-json <export.json>` instead of a command. A completed job with no steps and no assigned runner has no execution evidence; it is not a unit-test failure. Missing or mismatched SHAs fail admission.

**External step:** inspect that run's job annotation and the repository/account's Actions status. Establish the actual admission cause, have the authorized administrator resolve it, then retain a started current-revision run and its logs. YAML edits or this receipt cannot resolve billing, organization policy or runner capacity. No automated rerun or account change is performed by the diagnostic tool.

Generate current documentation from the scoped receipts. Historical counts remain historical; failed, skipped and not-run capabilities must remain visible. Official reference: https://docs.github.com/en/rest/actions/workflow-jobs .

## Exact revision admission

The receipt helper now fails closed if the supplied SHA differs from Git HEAD,
Git is unavailable, the source root differs, the index or worktree is dirty,
or the named command changes that source during execution. Keep disposable
untracked logs and exported job metadata outside the checkout when generating
an official revision-bound receipt. Existing historical receipts are not
retroactively granted these stricter guarantees.
