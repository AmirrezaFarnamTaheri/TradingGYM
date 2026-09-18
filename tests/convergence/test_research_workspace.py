from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading_gym.research.dossier import build_dossier
from trading_gym.research.models import EvidenceItem, EvidenceQuality
from trading_gym.research.quality import assess_dossier
from trading_gym.research.workspace import (
    ResearchWorkspace,
    WatchlistEntry,
    WorkspaceConflict,
)


def evidence(
    identifier: str, *, days_old: int, quality: EvidenceQuality
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=identifier,
        title=identifier,
        source="source",
        observed_at=datetime.now(timezone.utc) - timedelta(days=days_old),
        quality=quality,
        facts=["A fact."],
        metadata={"category": "market"},
    )


def test_research_quality_reports_coverage_freshness_and_missing_sections() -> None:
    dossier = build_dossier(
        subject="BTC",
        evidence=[
            evidence("fresh", days_old=1, quality=EvidenceQuality.PRIMARY),
            evidence("old", days_old=90, quality=EvidenceQuality.UNKNOWN),
        ],
    )
    assessment = assess_dossier(dossier, freshness_days=30)
    assert assessment.evidence_count == 2
    assert assessment.fresh_ratio == 0.5
    assert "news" in assessment.missing_sections
    assert "required research sections are missing" in assessment.warnings


def test_workspace_watchlist_notes_reports_and_cas(tmp_path: Path) -> None:
    workspace = ResearchWorkspace(tmp_path / "workspace.json")
    saved = workspace.save_watchlist(
        WatchlistEntry(instrument_id="BINANCE:BTCUSDT", tags=["core"]),
        expected_revision=0,
    )
    assert saved["revision"] == 1
    with pytest.raises(WorkspaceConflict):
        workspace.save_watchlist(
            WatchlistEntry(instrument_id="BINANCE:ETHUSDT"),
            expected_revision=0,
        )

    note = workspace.save_note(subject="Thesis", body="Evidence-backed note")
    revised = workspace.save_note(
        subject="Thesis",
        body="Revised",
        note_id=note.note_id,
        expected_revision=note.revision,
    )
    assert revised.revision == 2

    dossier = build_dossier(
        subject="BTC",
        evidence=[evidence("market-1", days_old=1, quality=EvidenceQuality.PRIMARY)],
    )
    report = workspace.save_report(dossier, label="daily")
    snapshot = workspace.snapshot()
    assert report["report_id"]
    assert (
        len(snapshot["watchlist"])
        == len(snapshot["notes"])
        == len(snapshot["reports"])
        == 1
    )
