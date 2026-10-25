"""BCa confidence intervals wired into research dossier generation."""

from datetime import datetime, timezone

import numpy as np
import pytest

from trading_gym.agent.research.dossier import build_dossier
from trading_gym.agent.research.models import EvidenceItem, EvidenceQuality


def _item(evidence_id: str, metadata: dict, section: str = "risk") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        title=f"Evidence {evidence_id}",
        source="backtest",
        observed_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
        quality=EvidenceQuality.PRIMARY,
        facts=[f"{evidence_id} reports a strategy statistic."],
        metadata={"category": section, **metadata},
    )


def _returns(seed: int = 3, n: int = 250) -> list[float]:
    rng = np.random.default_rng(seed)
    return (0.001 + 0.01 * rng.standard_normal(n)).tolist()


def test_returns_metadata_produces_bca_finding() -> None:
    evidence = [
        _item("risk:sharpe", {"returns": _returns(), "returns_statistic": "sharpe"})
    ]
    dossier = build_dossier(subject="BCa wiring", evidence=evidence)
    risk_section = next(s for s in dossier.sections if s.name == "risk")
    bca_lines = [f for f in risk_section.findings if "BCa" in f]
    assert len(bca_lines) == 1
    line = bca_lines[0]
    assert "[risk:sharpe]" in line
    assert "95%" in line and "sharpe" in line
    # Interval must bracket the point estimate.
    assert "[" in line and "," in line
    assert risk_section.limitations == []


def test_bca_intervals_are_deterministic_for_fixed_payloads() -> None:
    evidence = [_item("risk:a", {"returns": _returns(9)})]
    first = build_dossier(subject="det", evidence=evidence)
    second = build_dossier(subject="det", evidence=evidence)
    facts_a = next(s for s in first.sections if s.name == "risk").findings
    facts_b = next(s for s in second.sections if s.name == "risk").findings
    assert facts_a == facts_b


def test_items_without_returns_are_untouched() -> None:
    evidence = [_item("news:plain", {"headline": "nothing happened"}, section="news")]
    dossier = build_dossier(
        subject="plain", evidence=evidence, required_sections=("news",)
    )
    news_section = dossier.sections[0]
    assert not any("BCa" in f for f in news_section.findings)
    assert not any("bootstrap" in c.lower() for c in dossier.caveats)


def test_degenerate_series_flags_a_limitation_not_precision() -> None:
    evidence = [_item("risk:flat", {"returns": [0.01] * 60})]
    dossier = build_dossier(subject="degenerate", evidence=evidence)
    risk_section = next(s for s in dossier.sections if s.name == "risk")
    assert any(
        "point estimate" in limitation for limitation in risk_section.limitations
    )
    assert any("BCa" in f for f in risk_section.findings)


def test_malformed_payloads_fail_loudly_with_evidence_identity() -> None:
    bad_type = _item("risk:bad", {"returns": {"not": "a series"}})
    with pytest.raises(ValueError, match="risk:bad"):
        build_dossier(subject="x", evidence=[bad_type])

    too_short = _item("risk:short", {"returns": [0.01]})
    with pytest.raises(ValueError, match="at least two"):
        build_dossier(subject="x", evidence=[too_short])

    non_finite = _item("risk:nan", {"returns": [0.01, float("nan"), 0.02]})
    with pytest.raises(ValueError, match="finite"):
        build_dossier(subject="x", evidence=[non_finite])

    unknown_stat = _item(
        "risk:stat", {"returns": _returns(), "returns_statistic": "sortino"}
    )
    with pytest.raises(ValueError, match="unknown returns_statistic"):
        build_dossier(subject="x", evidence=[unknown_stat])


def test_mean_statistic_and_confidence_override() -> None:
    evidence = [
        _item("risk:mean", {"returns": _returns(), "returns_statistic": "mean"})
    ]
    dossier = build_dossier(
        subject="mean",
        evidence=evidence,
        bca_confidence=0.80,
        bca_n_resamples=200,
    )
    risk_section = next(s for s in dossier.sections if s.name == "risk")
    line = next(f for f in risk_section.findings if "BCa" in f)
    assert "80%" in line and "mean" in line


def test_bca_disabled_by_none_keeps_legacy_output() -> None:
    evidence = [_item("risk:off", {"returns": _returns()})]
    dossier = build_dossier(subject="off", evidence=evidence, bca_confidence=None)
    risk_section = next(s for s in dossier.sections if s.name == "risk")
    assert not any("BCa" in f for f in risk_section.findings)

    with pytest.raises(ValueError, match="bca_confidence"):
        build_dossier(subject="bad", evidence=[], bca_confidence=1.4)


def test_bootstrap_caveat_present_when_returns_were_bootstrapped() -> None:
    evidence = [_item("risk:cav", {"returns": _returns()})]
    dossier = build_dossier(subject="caveat", evidence=evidence)
    assert any("i.i.d." in caveat for caveat in dossier.caveats)
