"""Evidence-first research, quality, and durable workspace utilities."""

from trading_gym.agent.research.correlation_regimes import (
    classify_density_timeline,
    correlation_regimes,
)
from trading_gym.agent.research.dossier import build_dossier
from trading_gym.agent.research.quality import ResearchQualityAssessment, assess_dossier
from trading_gym.agent.research.reflection import audit_claims
from trading_gym.agent.research.workspace import (
    ResearchNote,
    ResearchWorkspace,
    WatchlistEntry,
    WorkspaceConflict,
)

__all__ = [
    "classify_density_timeline",
    "correlation_regimes",
    "ResearchNote",
    "ResearchQualityAssessment",
    "ResearchWorkspace",
    "WatchlistEntry",
    "WorkspaceConflict",
    "assess_dossier",
    "audit_claims",
    "build_dossier",
]
