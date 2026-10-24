#!/usr/bin/env bash
set -euo pipefail

export TRADING_GYM_REQUIRE_REAL_DEPS=1

for executable in node npm; do
  if ! command -v "$executable" >/dev/null 2>&1; then
    echo "Release validation requires $executable; install the supported Node.js runtime." >&2
    exit 1
  fi
done

python scripts/verify_release_manifest.py
python scripts/verify_workflow_pins.py
python scripts/verify_release_permissions.py
python scripts/export_openapi.py --check
python scripts/audit_module_accountability.py --check
python scripts/audit_recovered_target_coverage.py --check
python -m compileall -q trading_gym axiomforge apps scripts
python -m pytest -q
python scripts/run_contract_tests.py

node --test frontend/src/wallets/trustWallet.node.test.js frontend/src/jobResponse.node.test.mjs
npm ci --prefix frontend
npm run test:ci --prefix frontend
npm run build --prefix frontend
