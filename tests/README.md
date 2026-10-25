# Verification

Install Python 3.11/3.12 dependencies with `poetry install --with dev`. Optional `--with experiments` adds ONNX experiments. Then run:

```sh
poetry run pytest -q -m "not integration"
poetry run python scripts/verify_feature_fixtures.py
poetry run python scripts/verify_workflow_pins.py
poetry run python scripts/export_openapi.py --check
npm run test:ci --prefix frontend
npm run build --prefix frontend
cargo test --manifest-path kernel/Cargo.toml
```

`scripts/run_full_suite.sh` groups the local checks for Bash environments. CI sets `TRADING_GYM_REQUIRE_REAL_DEPS=1` so missing Torch, Stable-Baselines3 or Typer cannot silently select lightweight substitutes. Integration tests require their documented external services and are not covered by the non-integration command. Native parity requires a compiled kernel. Windows symlink tests skip only when the OS denies creation privileges.

`tests/agent` exercises accounting, risk, journal persistence, model rotation and actual local HTTP behavior. `tests/api` covers local boundaries, secret redaction, controls and history. `tests/system` retains migrated research/system contracts; `tests/environment` covers environment invariants. Frontend tests cover navigation, controls, decision inspection and Persian RTL using isolated response fixtures. These are test inputs, never production history.

Passing local tests does not qualify external Abantether schemas, a funded account, model quality, Docker networking, or a remote release. See [current rebuild evidence](../docs/REBUILD.md).
