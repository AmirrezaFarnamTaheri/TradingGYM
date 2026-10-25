"""Durable experiment jobs and immutable reproducibility manifests."""

from trading_gym.rl.experiments.models import (
    ExperimentCreate,
    ExperimentRecord,
    ExperimentState,
)
from trading_gym.rl.experiments.repository import ExperimentConflict, ExperimentRepository
from trading_gym.rl.experiments.reproducibility import (
    ArtifactReference,
    EnvironmentFingerprint,
    ManifestConflict,
    RunManifest,
    RunManifestCreate,
    RunManifestRepository,
    SeedBundle,
    build_manifest,
    capture_environment,
    hash_source_tree,
    verify_manifest,
)
from trading_gym.rl.experiments.leakage import (
    LeakagePolicy,
    TemporalLeakageReport,
    TemporalLeakageRequest,
    TemporalObservation,
    TemporalSplit,
    audit_temporal_leakage,
)

__all__ = [
    "ArtifactReference",
    "HypothesisResult",
    "SequentialEvidenceReport",
    "SequentialEvidenceRequest",
    "SequentialLook",
    "EnvironmentFingerprint",
    "ExperimentConflict",
    "ExperimentCreate",
    "ExperimentRecord",
    "ExperimentRepository",
    "ExperimentState",
    "ManifestConflict",
    "LeakagePolicy",
    "RunManifest",
    "RunManifestCreate",
    "RunManifestRepository",
    "SeedBundle",
    "TemporalLeakageReport",
    "TemporalLeakageRequest",
    "TemporalObservation",
    "TemporalSplit",
    "audit_temporal_leakage",
    "build_manifest",
    "capture_environment",
    "hash_source_tree",
    "verify_manifest",
    "evaluate_sequential_evidence",
]

from trading_gym.rl.experiments.sequential import (
    HypothesisResult,
    SequentialEvidenceReport,
    SequentialEvidenceRequest,
    SequentialLook,
    evaluate_sequential_evidence,
)

from trading_gym.rl.experiments.model_risk import (
    ChallengeRun,
    ModelChallengeReport,
    ModelChallengeRequest,
    evaluate_model_challenge,
)

__all__ += [
    "ChallengeRun",
    "ModelChallengeReport",
    "ModelChallengeRequest",
    "evaluate_model_challenge",
]
