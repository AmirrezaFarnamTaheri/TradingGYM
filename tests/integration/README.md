# Integration Tests

Integration tests that require external dependencies (API access, network, etc.).

## Setup

### 1. Install Test Dependencies

```bash
pip install pytest pytest-asyncio pytest-cov
```

### 2. Set Environment Variables

For SDK integration tests:
```bash
export ABAN_API_KEY="your_jwt_token_here"
```

## Running Tests

### Run All Integration Tests
```bash
pytest tests/integration/ -v
```

### Run Specific Test File
```bash
pytest tests/integration/test_sdk_integration.py -v
```

### Run with Coverage
```bash
pytest tests/integration/ -v --cov=lib --cov-report=html
```

### Run Specific Test
```bash
pytest tests/integration/test_sdk_integration.py::TestAbanSDKWrapper::test_get_balance -v
```

## Test Organization

- `test_sdk_integration.py` - Tests for Aban-Tether SDK wrapper
  - Synchronous wrapper tests
  - Rate-limited async wrapper tests
  - Error handling tests
  - Data format validation
  - Performance tests

## Notes

### Skip Destructive Tests

Some tests (like order placement) are skipped by default to prevent accidental trades.
To run in sandbox environment:

1. Set up sandbox API key
2. Remove `@pytest.mark.skip` decorator
3. Run tests in sandbox only

### Rate Limiting

The performance tests validate rate limiting behavior. They take time to run
(~10 seconds for sustained rate limit test). Use `-k "not Performance"` to skip:

```bash
pytest tests/integration/ -v -k "not Performance"
```

## Continuous Integration

For CI/CD pipelines:

```yaml
# .github/workflows/test.yml (example)
- name: Run Integration Tests
  env:
    ABAN_API_KEY: ${{ secrets.ABAN_API_KEY }}
  run: pytest tests/integration/ -v
```

## Troubleshooting

### API Key Not Set
```
SKIPPED [1] tests/integration/test_sdk_integration.py:20: ABAN_API_KEY environment variable not set
```
**Solution**: Set the environment variable

### Network Errors
If tests fail due to network issues, check:
1. Internet connection
2. API endpoint availability
3. Firewall settings

### Rate Limit Errors
If you hit rate limits during development:
1. Wait a minute before retrying
2. Reduce number of test runs
3. Use mocks for rapid iteration (see unit tests)
