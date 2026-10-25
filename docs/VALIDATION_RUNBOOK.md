# Local validation

Use the commands in [tests/README](../tests/README.md), or `bash scripts/run_full_suite.sh` in a Bash environment. Install locked dependencies first and set `TRADING_GYM_REQUIRE_REAL_DEPS=1` when verifying the complete ML runtime.

The supported checks cover Python non-integration tests, generated API contracts, original feature-fixture hashes, immutable workflow pins, frontend tests/build/audit and Rust tests. Native parity additionally requires the compiled kernel. Optional ONNX experiments require the experiments dependency group. Record command, date, working-tree state, exit status and exclusions with ordinary logs; do not include secrets.

The old validation-receipt/report-inventory tooling has been retired. It is not a prerequisite for local operation. A successful subset does not certify a full release, external account, venue schema or model profitability. Skipped and unexecuted checks must remain visible. Current local results and limits are in [REBUILD](REBUILD.md).

For hosted CI, inspect the exact revision's jobs and all relevant pages of logs. A job waiting for a runner, a skipped gate or a workflow that never started is not a passing test. Resolve account/runner restrictions separately; local YAML changes cannot establish their cause. This task did not publish or trigger a remote release.
