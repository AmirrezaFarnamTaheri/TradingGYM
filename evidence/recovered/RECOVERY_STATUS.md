# AxiomForge 31.7.0 recovery status

This bundle is the latest AxiomForge / trading_gym state recoverable from retained ChatGPT Library artifacts as of 2026-08-23.

## Version identity

- Latest explicit recovered version: AxiomForge 31.7.0.
- `AxiomForge_31.7.0_OSS_Convergence_Adoption_Ledger.csv` is the retained versioned convergence ledger.
- The post-audit adoption ledger records F-001 through F-018 remediation/accountability items. F-001 through F-016 and F-018 are recorded as verified; F-017 is recorded as statically validated.

## Recovered implementation/release evidence

- AxiomForge_31.7.0_OSS_Convergence_Adoption_Ledger.csv
- AxiomForge_post_audit_remediation_ADOPTION.csv
- aban-qualification.yml
- wallet-qualification.yml
- chain-qualification.yml
- production-qualification.yml
- first_batch.py
- hermetic_build.py
- FINDINGS.csv
- DEEP_INVESTIGATION_CANVAS.html

The qualification workflows cover Aban, Trust Wallet / WalletConnect, chain safety, and production evidence aggregation. The hermetic build helper covers dependency-lock verification, Python compilation/tests, and frontend tests/build.

## Important boundary

The retained Library does not contain the complete AxiomForge 31.7.0 source tree, source-release ZIP, wheel, or matching release receipt. Therefore this archive is a recovery/evidence bundle, not a runnable full-source release. No older Trading Gym archive has been substituted or mislabeled as 31.7.0.

The earlier 31.3 audit is included only as historical pre-remediation evidence; later adoption and qualification artifacts supersede its unresolved findings where those items are recorded as repaired/verified.
