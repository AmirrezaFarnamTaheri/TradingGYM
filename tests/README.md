# Tests

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
