# Tests

## Run the supported gates

Use Python 3.11 or 3.12 and install the checked-in dependency lock:

```sh
poetry install --with dev,experiments
poetry run python scripts/run_contract_tests.py
poetry run pytest -q
```

The optional `experiments` group supplies the CPU ONNX export/runtime packages.
Ordinary application and development installs do not need it. The ONNX
experiment tests report a missing optional dependency as a skip in an ordinary
test run. The separate contract gate requires those packages to import
successfully before running tests, so a missing experiment runtime cannot turn
that gate green.

`run_contract_tests.py` exercises the research, paper-account, execution,
intelligence, API and delivery boundaries with real installed dependencies and
`--noconftest`. It uses a fresh temporary application home and prints the actual
Python/package versions. Use `--list` to inspect its selected files without
importing application dependencies. Additional pytest arguments, such as
`--junitxml=artifacts/contracts.xml`, are forwarded literally.

The complete legacy suite keeps its shared fixtures. Set
`TRADING_GYM_REQUIRE_REAL_DEPS=1` when validating an installed environment; this
rejects missing/substitute Torch, Stable-Baselines3 or Typer before the root
conftest can install lightweight substitutes. CI and the release gate set it.
The focused contract suite and full suite have different coverage and both are
required for release qualification.

For all source, schema, frontend and Python release checks, run:

```sh
poetry run bash scripts/run_full_suite.sh
```

This command requires the experiment group and a supported Node/npm installation.
It does not qualify Docker installation, Windows behavior, real venue/provider
operation or strategy returns. Record those checks separately when executed.

This directory contains the test suite for the trading system. The tests are organized by feature area, mirroring the structure of the `/apps` and `/trading_gym` directories.

## Core Principles

- **Pytest**: The test suite is built on the `pytest` framework.
- **Feature-Based Organization**: Tests are organized into subdirectories based on the feature they are testing (e.g., `tests/margin`, `tests/execution`).
- **Deterministic Tests**: The tests are designed to be deterministic, with a focus on verifying the correctness of the core logic under a variety of controlled scenarios.

## Testing Strategy

The testing strategy is focused on ensuring the correctness and robustness of the core trading logic. This is achieved through a combination of:

- **Unit Tests**: Each component of the system is tested in isolation to verify its functionality.
- **Integration Tests**: Multiple components are tested together to ensure they interact correctly.
- **Scenario Tests**: The system is tested against a variety of pre-defined scenarios, such as flash crashes and spread blowouts, to ensure it behaves as expected under adverse conditions.

A key aspect of the testing strategy is the use of the "Freeze the World" method, as described in the project's core directives. This involves simplifying the test environment to a minimal state to verify core logic, then systematically re-introducing complexity to isolate bugs.
