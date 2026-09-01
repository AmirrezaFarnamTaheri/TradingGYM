# Trading Gym / AxiomForge 31.7.0+reconstructed.1

## Release identity

This is the **final reconstructed converged source release** built on the uploaded Trading Gym 11.0.0 source archive (`SHA-256 7a3e110e6e046376eb9866ccc4397ada282473ba66f29bb56ec01fd6ae251bb8`). It reconstructs the latest recovered Trading Gym/AxiomForge behavior and target ownership through the 31.7.0 remediation line.

It is **not** represented as the byte-identical historical 31.7.0 source tree. Later source bytes were not retained; later adoption ledgers, qualification artifacts, target paths, invariants, acceptance evidence, and the intact v11 source were used to re-derive target-native implementations.

Recovered lineage: `23.1.0 -> 24.0.0 -> 25.0.0 -> 26.0.0 -> 27.0.0 -> 28.0.0 -> 29.0.0 -> 30.0.0 -> AxiomForge 31.7.0`.

## What is converged

- A single governed live mutation path: startup authorization/live consent -> payload-bound `LiveExecutionCoordinator` -> Aban venue transport -> SDK.
- Exact-flat execution accounting for true flatten requests.
- Authoritative Aban policy/config identity with duplicate limiter/retry authority removed.
- Durable emergency halt integrated with the sole live coordinator and authenticated API/operator surfaces.
- Governed model promotion, decision lifecycle, regime-vote rationales, configuration hashing, noncustodial wallet/chain registry, and Trust Wallet projection.
- Generated OpenAPI truth, immutable GitHub Action pins, blocking release gates, source-release identity, executable-bit verification, and module accountability.
- All 25 reconstructed late-wave assurance modules wired through one authenticated, closed-world, advisory-only registry.
- Recovered peer-wave catalog/search/saturation surfaces retained as non-authoritative evidence; wave 18 fails closed where its source surface matrix is unavailable.
- AxiomForge OSS integration seams reconstructed as target-native optional integrations rather than donor-shaped competing authorities.

## Validation

- Broad dependency-available Python regression: **767 passed, 11 skipped, 4 subtests passed, 0 failures**.
- Release-manifest acceptance set: **76 passed, 2 skipped, 4 subtests passed, 0 failures**. Both skips explicitly require process-level authentication isolation.
- Recovered target-node accountability: **135/135 target paths present** across **288 recovered ledger rows**.
- Product Python module accountability: **273 modules** hash-accounted.
- Generated OpenAPI: **193 paths**, including authenticated production and late-wave assurance endpoints.
- Late-wave assurance registry: **25/25 reconstructed assurance modules registered**, all advisory-only and mutation-incapable.
- GitHub workflow uses: all pinned to immutable 40-character commit SHAs.
- Shebang/executable permission gate: passed.
- Dependency-free Trust Wallet Node test: passed.

One composite validation command hit its outer 120-second command timeout after printing a transient failure progress marker but no traceback. The immediately isolated full broad rerun completed cleanly at 767 passed / 11 skipped; the marker did not reproduce.

## Validation — 31.7.1 remediation pass (2026-08-24)

- Full dependency-complete Python regression (Python 3.12.14, all optional deps
  installed): **1060 passed, 3 skipped, 4 subtests passed, 0 failures**. The ten
  dependency-gated exclusions recorded in `VALIDATION_RECEIPT.json` no longer apply:
  every previously excluded area now runs in-suite.
- Frontend: Vitest **7/7 passing** via the exact CI command (`npm run test:ci`) and
  production Vite build green (157 modules). Operator workspace capability F-017 is
  therefore **runtime-verified**, upgrading the prior static-only claim.
- New in this pass, all covered by tests: automatic replay-safe mutation retry
  (`trading_gym/production/mutation_retry.py` + coordinator ambiguous-key fail-closed
  gate + durable outbox journal), broker-style `margin_level` metric, pluggable reward
  library (`trading_gym/rl/rewards.py`), and hypothesis-based execution-engine
  invariant oracles.
- Product module accountability regenerated after changes: **296 modules**
  hash-accounted (`docs/contracts/module_accountability.json`).
- P0 defects found and fixed during this pass are enumerated in CHANGELOG `[31.7.1]`,
  notably: env/engine liquidity-cap contract conflict (every env step raised),
  z-scored observation features leaking into execution microstructure math, a pandas-3
  timestamp-resolution bug in poller interval validation, Starlette >=0.46 router
  wrapper breakage in surface tests, and a pytest-9 fixture-mark collection blocker.

---

## Explicit limitations

1. `Trading_Gym_30.0.0_SURFACES.csv` was not recovered. The claimed 13,238 wave-18 surfaces therefore are **not reconstructed or fabricated**; wave-18 saturation stays fail-closed.
2. The validation container runs Python 3.13.5 while the project declares Python `>=3.10,<3.13`. Locked `hypothesis 6.147.0`, `gymnasium 0.29.1`, and `pyarrow 15.0.2` are unavailable in this container, and network installation is blocked. Ten dependency-gated test files therefore were excluded from the dependency-available broad run and are listed in `VALIDATION_RECEIPT.json`.
3. Frontend `node_modules` are absent and network installation is blocked, so the full Vitest/Vite frontend run was not executable locally. The operator workspace capability F-017 is therefore `statically-validated`, not runtime-verified. The dependency-free Trust Wallet Node test passed, and release CI provisions Node 20 and runs `npm ci`, Vitest, and the production build.
4. The original `gm-crypto-rs` donor source bytes were not recovered. Its `third_party` placeholder is deliberately inert and must not be interpreted as recovered executable donor code.
5. No real credential-bearing venue, wallet, chain, payment, deployment, or other external production side effect was exercised.

## Release truth

The authoritative machine-readable reconstruction identity is `trading_gym/release_manifest.json`. `VALIDATION_RECEIPT.json` records the executed proof boundary. `docs/contracts/recovered_target_coverage.json`, `docs/contracts/module_accountability.json`, and `docs/contracts/openapi.json` are deterministic checked artifacts. `SHA256SUMS` binds the final source contents after freeze.
