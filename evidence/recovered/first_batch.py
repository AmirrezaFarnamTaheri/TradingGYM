"""Evidence-backed source and external qualification state for the first batch."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from scripts.verify_dependency_locks import analyze_dependency_locks
from trading_gym.compliance.jurisdiction import JurisdictionPolicy
from trading_gym.qualification.aban import AbanContractBaseline
from trading_gym.qualification.aban_authority import AbanAuthorityProfileCatalog
from trading_gym.qualification.operations import OperationsReadinessCatalog
from trading_gym.qualification.release_identity import ReleaseIdentityPolicy
from trading_gym.qualification.staging import StagingTopology
from trading_gym.qualification.external_evidence import ExternalEvidenceEnvelope

JOBS = (1, 2, 3, 6, 7, 10, 11, 13, 14, 15, *range(16, 25))
EXTERNAL_EVIDENCE: dict[int, str] = {
    2: "runtime-images.json",
    3: "release-signatures.json",
    11: "staging-deployment.json",
    13: "database-recovery-drill.json",
    14: "load-chaos-soak.json",
    15: "incident-game-day.json",
    16: "aban-e2e.json",
    17: "aban-websocket.json",
    20: "aban-settlement.json",
    21: "trust-wallet-extension.json",
    22: "walletconnect-mobile.json",
    23: "rpc-quorum.json",
    24: "transaction-safety.json",
}

_EXTERNAL_PRODUCERS: dict[int, tuple[str, str]] = {
    2: ("verified-release-runtime-images", "production-qualification"),
    3: ("repository-bound-sigstore-verification", "production-qualification"),
    11: ("staging-qualification-workflow", "staging"),
    13: ("postgres-recovery-drill", "staging"),
    14: ("resilience-qualification", "staging"),
    15: ("incident-game-day", "staging"),
    16: ("aban-sandbox-certification", "certification"),
    17: ("aban-websocket-certification", "certification"),
    20: ("aban-settlement-attestation", "certification"),
    21: ("trust-wallet-extension-certification", "certification"),
    22: ("walletconnect-mobile-certification", "certification"),
    23: ("rpc-quorum-certification", "certification"),
    24: ("transaction-safety-certification", "certification"),
}

_REQUIRED_PATHS: dict[int, tuple[str, ...]] = {
    1: (
        "scripts/verify_dependency_locks.py",
        "scripts/hermetic_build.py",
        "tests/platform/test_dependency_lock_policy.py",
        "tests/platform/test_hermetic_build.py",
        "docs/HERMETIC_BUILDS.md",
    ),
    2: (
        "config/runtime_image_policy.json",
        "scripts/generate_runtime_image_manifest.py",
        "scripts/verify_runtime_images.py",
        "docker/Dockerfile.worker",
        "docker/Dockerfile.migration",
        ".github/workflows/release.yml",
        ".github/workflows/production-qualification.yml",
    ),
    3: (
        "config/release_identity_policy.json",
        "scripts/verify_release_identity.py",
        "scripts/verify_release_signatures.sh",
        "scripts/build_evidence_provenance_manifest.py",
        "trading_gym/qualification/run_provenance.py",
        ".github/workflows/release.yml",
        ".github/workflows/production-qualification.yml",
    ),
    6: (
        "trading_gym/governance/access_control.py",
        "trading_gym/governance/identity.py",
        "tests/qualification/test_identity_secrets_compliance.py",
    ),
    7: (
        "trading_gym/security/secret_lifecycle.py",
        "trading_gym/production/credentials.py",
        "tests/qualification/test_identity_secrets_compliance.py",
    ),
    10: (
        "config/jurisdiction_policy.json",
        "config/public_claims_policy.json",
        "trading_gym/compliance/jurisdiction.py",
        "scripts/verify_product_claims.py",
    ),
    11: (
        "deploy/staging/docker-compose.staging.yml",
        ".github/workflows/staging-qualification.yml",
        ".github/workflows/production-qualification.yml",
        "scripts/verify_staging_topology.py",
        "scripts/evaluate_staging_deployment.py",
    ),
    13: (
        "trading_gym/qualification/database_recovery.py",
        "trading_gym/qualification/postgres_recovery.py",
        "scripts/run_database_recovery_drill.py",
        "scripts/evaluate_postgres_recovery.py",
    ),
    14: (
        "config/resilience_qualification.json",
        "trading_gym/qualification/chaos.py",
        "scripts/run_resilience_qualification.py",
        "scripts/evaluate_resilience_external.py",
    ),
    15: (
        "config/operations_readiness.json",
        "trading_gym/qualification/operations.py",
        "scripts/verify_operations_readiness.py",
        "scripts/evaluate_incident_game_day.py",
    ),
    16: (
        "trading_gym/qualification/aban.py",
        ".github/workflows/aban-qualification.yml",
        "scripts/evaluate_aban_certification.py",
        "config/qualification_templates/aban-certification.json",
    ),
    17: (
        "trading_gym/adapters/aban_websocket.py",
        "trading_gym/qualification/aban.py",
        ".github/workflows/aban-qualification.yml",
        "tests/qualification/test_aban_program.py",
        "scripts/evaluate_aban_websocket.py",
    ),
    18: (
        "config/aban_authority_profiles.json",
        "trading_gym/adapters/aban_authority.py",
        "tests/adapters/test_aban_authority_runtime.py",
    ),
    19: (
        "config/aban_contract_baseline.json",
        "scripts/check_aban_contract_drift.py",
        "tests/qualification/test_aban_program.py",
    ),
    20: (
        ".github/workflows/aban-qualification.yml",
        "scripts/attest_aban_settlement.py",
        "tests/qualification/test_aban_program.py",
        "trading_gym/accounting/ledger.py",
    ),
    21: (
        ".github/workflows/wallet-qualification.yml",
        "frontend/src/wallets/trustWallet.js",
        "frontend/src/wallets/trustWallet.node.test.js",
        "scripts/evaluate_wallet_certification.py",
    ),
    22: (
        ".github/workflows/wallet-qualification.yml",
        "frontend/src/wallets/trustWallet.js",
        "config/qualification_templates/walletconnect-mobile.json",
        "scripts/evaluate_wallet_certification.py",
    ),
    23: (
        ".github/workflows/chain-qualification.yml",
        "trading_gym/production/chain.py",
        "trading_gym/qualification/chain_safety.py",
        "scripts/evaluate_chain_certification.py",
        "scripts/evaluate_rpc_quorum.py",
    ),
    24: (
        ".github/workflows/chain-qualification.yml",
        "trading_gym/production/chain.py",
        "trading_gym/qualification/chain_safety.py",
        "tests/qualification/test_chain_runtime_integration.py",
        "scripts/evaluate_transaction_safety.py",
    ),
}


class FirstBatchJobReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job: int
    source_complete: bool
    source_blockers: tuple[str, ...] = ()
    external_evidence_required: str | None = None
    state: Literal[
        "source_blocked",
        "source_complete",
        "blocked_external_evidence",
        "external_evidence_failed",
        "passed",
    ]
    evidence: dict[str, Any] | None = None
    authority_granted: bool = False


class FirstBatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    jobs: dict[str, FirstBatchJobReport]
    source_blockers: tuple[str, ...] = ()
    all_source_complete: bool
    all_externally_qualified: bool
    authority_granted: bool = False


class EvidenceRunExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_path: str = Field(pattern=r"^\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml$")
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    artifact_name: str = Field(min_length=1, max_length=250)


class EvidenceProvenanceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    jobs: dict[str, EvidenceRunExpectation]

    @classmethod
    def from_json(cls, path: Path) -> "EvidenceProvenanceManifest":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("qualification evidence must be a JSON object")
    return value


def _external_passed(
    document: dict[str, Any],
    *,
    expected_job: int,
    provenance: EvidenceProvenanceManifest | None,
) -> bool:
    if provenance is None:
        return False
    expectation = provenance.jobs.get(str(expected_job))
    if expectation is None:
        return False
    envelope = ExternalEvidenceEnvelope.model_validate(document)
    expected_producer, expected_environment = _EXTERNAL_PRODUCERS[expected_job]
    now = datetime.now(timezone.utc)
    age = now - envelope.collected_at
    fresh = -timedelta(minutes=5) <= age <= timedelta(days=7)
    return (
        envelope.job == expected_job
        and envelope.producer == expected_producer
        and envelope.environment == expected_environment
        and envelope.repository == provenance.repository
        and envelope.workflow_path == expectation.workflow_path
        and envelope.run_id == expectation.run_id
        and envelope.run_attempt == expectation.run_attempt
        and envelope.commit_sha == provenance.commit_sha
        and envelope.artifact_name == expectation.artifact_name
        and fresh
        and envelope.passed
        and envelope.authority_granted is False
    )


def _source_blockers(root: Path, job: int) -> list[str]:
    blockers = [
        f"missing_source:{path}"
        for path in _REQUIRED_PATHS[job]
        if not (root / path).is_file()
    ]
    if blockers:
        return blockers
    try:
        if job == 1:
            report = analyze_dependency_locks(root)
            if report.get("status") != "verified":
                blockers.extend(
                    f"dependency_lock:{item.get('code', 'unknown')}"
                    for item in report.get("issues", ())
                )
        elif job == 3:
            ReleaseIdentityPolicy.from_json(
                root / "config/release_identity_policy.json"
            )
        elif job == 10:
            JurisdictionPolicy.from_json(root / "config/jurisdiction_policy.json")
            _load_json(root / "config/public_claims_policy.json")
        elif job == 11:
            staging = StagingTopology.from_yaml(
                root / "deploy/staging/docker-compose.staging.yml"
            ).evaluate()
            blockers.extend(staging.blockers)
        elif job == 15:
            operations = OperationsReadinessCatalog.from_json(
                root / "config/operations_readiness.json"
            ).evaluate(root=root)
            blockers.extend(operations.blockers)
        elif job == 18:
            AbanAuthorityProfileCatalog.from_json(
                root / "config/aban_authority_profiles.json"
            )
        elif job == 19:
            AbanContractBaseline.from_json(root / "config/aban_contract_baseline.json")
    except Exception as exc:  # source contract must fail closed with a stable message
        blockers.append(f"source_contract_invalid:{job}:{type(exc).__name__}:{exc}")
    return sorted(set(blockers))


def evaluate_first_batch(
    *,
    root: Path,
    evidence_dir: Path | None = None,
    provenance_manifest: Path | None = None,
) -> FirstBatchReport:
    root = root.resolve()
    evidence_dir = evidence_dir.resolve() if evidence_dir else None
    provenance = (
        EvidenceProvenanceManifest.from_json(provenance_manifest.resolve())
        if provenance_manifest
        else None
    )
    jobs: dict[str, FirstBatchJobReport] = {}
    global_blockers: list[str] = []
    for job in JOBS:
        blockers = _source_blockers(root, job)
        source_complete = not blockers
        required = EXTERNAL_EVIDENCE.get(job)
        evidence: dict[str, Any] | None = None
        if not source_complete:
            state = "source_blocked"
        elif not required:
            state = "source_complete"
        elif evidence_dir is None or not (evidence_dir / required).is_file():
            state = "blocked_external_evidence"
        else:
            try:
                evidence = _load_json(evidence_dir / required)
                state = (
                    "passed"
                    if _external_passed(
                        evidence, expected_job=job, provenance=provenance
                    )
                    else "external_evidence_failed"
                )
            except Exception as exc:
                evidence = {
                    "load_error": f"{type(exc).__name__}:{exc}",
                    "authority_granted": False,
                }
                state = "external_evidence_failed"
        global_blockers.extend(f"job_{job}:{item}" for item in blockers)
        jobs[str(job)] = FirstBatchJobReport(
            job=job,
            source_complete=source_complete,
            source_blockers=tuple(blockers),
            external_evidence_required=required,
            state=state,
            evidence=evidence,
            authority_granted=False,
        )
    return FirstBatchReport(
        jobs=jobs,
        source_blockers=tuple(sorted(global_blockers)),
        all_source_complete=all(item.source_complete for item in jobs.values()),
        all_externally_qualified=all(
            item.state in {"source_complete", "passed"} for item in jobs.values()
        ),
        authority_granted=False,
    )
