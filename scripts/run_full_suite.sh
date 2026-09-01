#!/usr/bin/env bash
set -euo pipefail

python scripts/verify_release_manifest.py
python scripts/verify_workflow_pins.py
python scripts/verify_release_permissions.py
python scripts/export_openapi.py --check
python scripts/audit_module_accountability.py --check
python scripts/audit_recovered_target_coverage.py --check
python -m compileall -q trading_gym axiomforge apps scripts
python -m pytest -q

if command -v node >/dev/null 2>&1; then
  node frontend/src/wallets/trustWallet.node.test.js
fi
if command -v npm >/dev/null 2>&1; then
  npm ci --prefix frontend
  npm run test:ci --prefix frontend
  npm run build --prefix frontend
fi
