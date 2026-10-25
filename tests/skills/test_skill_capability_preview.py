"""Digest-pinned skill suggestions never execute or grant capability."""

from copy import deepcopy

import pytest

from trading_gym.agent.skills.capability_selection import (
    catalogue_digest,
    preview_capability_selection,
)


def _skill(*, caps=("research.read",), owner="research_team", **updates):
    row = {
        "sha256": "a" * 64,
        "capabilities": list(caps),
        "owner": owner,
        "reviewer": "reviewer_a",
        "preparer": "preparer_b",
        "reviewed_at_unix": 100,
        "expires_at_unix": 300,
        "quarantined": True,
        "activated": False,
        "risk_markers": [],
    }
    row.update(updates)
    return row


def _preview(
    catalogue,
    *,
    scopes=("research.read",),
    digest=None,
    consent=True,
    now=200,
):
    return preview_capability_selection(
        catalogue,
        required_capabilities=scopes,
        expected_owner="research_team",
        operator_consent=consent,
        as_of_unix=now,
        expected_catalogue_sha256=(
            digest if digest is not None else catalogue_digest(catalogue)
        ),
    )


def test_minimum_permission_skill_is_only_a_non_authoritative_hint():
    entries = {
        "minimal": _skill(),
        "broad": _skill(caps=("research.read", "strategy.read")),
    }
    report = _preview(entries)
    assert report["selected_hint"] == "minimal"
    assert report["non_authoritative"] is True
    assert report["executed"] is False
    assert report["activated"] is False
    assert report["grant_created"] is False
    assert len(report["report_sha256"]) == 64


def test_missing_consent_or_mismatched_digest_never_suggests_a_skill():
    catalogue = {"research": _skill()}
    assert _preview(catalogue, consent=False)["reason"] == "operator_consent_required"
    report = _preview(catalogue, digest="b" * 64)
    assert report["selected_hint"] is None
    assert report["reason"] == "catalogue_digest_mismatch"


def test_expired_foreign_unsafe_and_unreviewed_skills_are_excluded():
    catalogue = {
        "expired": _skill(expires_at_unix=180),
        "foreign": _skill(owner="other_team"),
        "no_reviewer": _skill(reviewer=""),
        "same_reviewer": _skill(reviewer="preparer_b"),
        "activated": _skill(activated=True),
        "risk": _skill(risk_markers=["subprocess"]),
        "shell": _skill(caps=("research.read", "shell")),
        "bad_hash": _skill(sha256="z" * 64),
        "missing": _skill(caps=("strategy.read",)),
    }
    result = _preview(catalogue)
    assert result["selected_hint"] is None
    assert set(result["excluded"]) == set(catalogue)
    assert "expired_review" in result["excluded"]["expired"]
    assert "owner_mismatch" in result["excluded"]["foreign"]
    assert "invalid_reviewer" in result["excluded"]["no_reviewer"]
    assert "reviewer_not_independent" in result["excluded"]["same_reviewer"]
    assert "missing_required_capability" in result["excluded"]["missing"]


def test_catalogue_hash_and_selection_are_order_independent():
    entries = {"zeta": _skill(), "alpha": _skill()}
    original = deepcopy(entries)
    first = _preview(entries)
    second = _preview(dict(reversed(list(entries.items()))))
    assert first == second
    assert first["selected_hint"] == "alpha"
    assert entries == original
    entries["zeta"]["expires_at_unix"] = 250
    assert catalogue_digest(entries) != first["catalogue_sha256"]


def test_privileged_requests_and_bad_metadata_fail_closed():
    catalogue = {"reader": _skill()}
    for scopes in (
        ("trade",),
        ("shell",),
        ("research.read", "research.read"),
        ("NETWORK.GET",),
    ):
        with pytest.raises(ValueError):
            _preview(catalogue, scopes=scopes)
    for consent in (None, 1, "yes"):
        with pytest.raises(ValueError):
            _preview(catalogue, consent=consent)
    for now in (True, -1, float("nan")):
        with pytest.raises(ValueError):
            _preview(catalogue, now=now)
    with pytest.raises(ValueError):
        _preview({"reader": _skill(), "invalid": {"sha256": object()}})


def test_extra_secret_bearing_fields_are_excluded_without_echoing_values():
    report = _preview({"unsafe": _skill(secret_token="do-not-echo")})
    assert report["selected_hint"] is None
    assert "unexpected_fields" in report["excluded"]["unsafe"]
    assert "do-not-echo" not in str(report)


def test_candidate_count_budget_is_enforced():
    oversized = {f"skill_{i}": _skill() for i in range(1025)}
    with pytest.raises(ValueError, match="limit"):
        catalogue_digest(oversized)


def test_registry_snapshot_matches_digest_pinned_selection():
    from trading_gym.agent.skills.quarantine import SkillQuarantineRepository

    quarantine = SkillQuarantineRepository()
    record = _skill()
    quarantine.admit("research", record, reviewer="reviewer_a", preparer="preparer_b")
    snapshot = quarantine.list()
    report = _preview(snapshot)
    assert report["selected_hint"] == "research"
    assert report["catalogue_sha256"] == catalogue_digest(snapshot)
