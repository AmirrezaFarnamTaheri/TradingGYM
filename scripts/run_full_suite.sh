#!/usr/bin/env bash
set -euo pipefail
export TRADING_GYM_REQUIRE_REAL_DEPS=1
python scripts/verify_feature_fixtures.py
python scripts/verify_workflow_pins.py
python scripts/export_openapi.py --check
python -m compileall -q trading_gym scripts
python -m pytest -q -m "not integration"
npm ci --prefix frontend
npm run test:ci --prefix frontend
npm run build --prefix frontend
npm audit --prefix frontend --audit-level=high
cargo test --manifest-path kernel/Cargo.toml
