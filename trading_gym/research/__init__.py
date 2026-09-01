"""Evidence-first research, quality, and durable workspace utilities."""

from trading_gym.research.dossier import build_dossier
from trading_gym.research.quality import ResearchQualityAssessment, assess_dossier
from trading_gym.research.reflection import audit_claims
from trading_gym.research.workspace import (
    ResearchNote,
    ResearchWorkspace,
    WatchlistEntry,
    WorkspaceConflict,
)

__all__ = [
    "ResearchNote",
    "ResearchQualityAssessment",
    "ResearchWorkspace",
    "WatchlistEntry",
    "WorkspaceConflict",
    "assess_dossier",
    "audit_claims",
    "build_dossier",
]
