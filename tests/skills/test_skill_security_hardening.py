"""Fail-closed static skill intake, quarantine, and workflow regressions."""

import pytest

from trading_gym.skills.intake import inspect_skill_package
from trading_gym.skills.quarantine import SkillQuarantineRepository
from trading_gym.skills.static_assurance import (
    assess_skill_static,
    assess_workflow_static,
)


def test_noncanonical_hash_and_untrusted_capabilities_are_denied():
    for value in ("z" * 64, "A" * 64, " " * 64, 64, None):
        result = assess_skill_static({"sha256": value, "capabilities": ["read"]})
        assert not result["ready_for_human_review"]
        assert "missing_hash" in result["blockers"]
    invalid = (
        "read",
        ["READ"],
        ["trade"],
        ["shell"],
        ["network.fetch"],
        ["read", "read"],
    )
    for caps in invalid:
        result = assess_skill_static({"sha256": "a" * 64, "capabilities": caps})
        assert not result["ready_for_human_review"]
        assert result["executed"] is False


def test_valid_readonly_manifest_remains_reviewable_but_not_executed():
    report = assess_skill_static({"sha256": "a" * 64, "capabilities": ("read",)})
    assert report == {
        "blockers": [],
        "ready_for_human_review": True,
        "executed": False,
    }


def test_quarantine_reviewer_hash_uniqueness_and_defensive_copies():
    repository = SkillQuarantineRepository()
    for reviewer, preparer in (
        ("alice", "ALICE"),
        (" ", "bob"),
        ("alice ", "bob"),
    ):
        with pytest.raises(ValueError):
            repository.admit(
                "research_reader",
                {"sha256": "a" * 64},
                reviewer=reviewer,
                preparer=preparer,
            )
    for sha in ("z" * 64, "", True):
        with pytest.raises(ValueError):
            repository.admit(
                "research_reader",
                {"sha256": sha},
                reviewer="alice",
                preparer="bob",
            )
    original = {"sha256": "a" * 64, "capabilities": ["read"]}
    saved = repository.admit(
        "research_reader", original, reviewer="alice", preparer="bob"
    )
    original["capabilities"].append("trade")
    saved["capabilities"].append("shell")
    repository.list()["research_reader"]["capabilities"].append("wallet_sign")
    assert repository.list()["research_reader"]["capabilities"] == ["read"]
    with pytest.raises(ValueError, match="duplicate"):
        repository.admit(
            "research_reader", original, reviewer="carol", preparer="dave"
        )


def test_intake_refuses_symlink_and_oversized_data(tmp_path):
    original = tmp_path / "original.json"
    original.write_text('{"name":"reader"}', encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(original)
    with pytest.raises(ValueError):
        inspect_skill_package(link)
    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as stream:
        stream.truncate(16_777_217)
    with pytest.raises(ValueError):
        inspect_skill_package(oversized)
    result = inspect_skill_package(original)
    assert result["quarantined"] and not result["activated"]
    assert len(result["sha256"]) == 64


def test_workflow_duplicate_steps_and_negative_retries_deny_review():
    workflow = {
        "steps": [
            {"id": "a", "retries": 1, "timeout_seconds": 60},
            {"id": "a", "retries": -1, "timeout_seconds": 60},
        ]
    }
    report = assess_workflow_static(workflow)
    assert not report["ready_for_human_review"]
    assert "duplicate_step" in report["blockers"]
    assert "retries_out_of_bounds" in report["blockers"]


def test_workflow_rejects_nan_boolean_strings_and_bad_retry_values():
    for timeout in (float("nan"), float("inf"), "NaN", True, -1, 0, "abc"):
        report = assess_workflow_static(
            {"steps": [{"id": "a", "retries": 0, "timeout_seconds": timeout}]}
        )
        assert not report["ready_for_human_review"]
    for retries in (-1, True, "oops", 11):
        report = assess_workflow_static(
            {"steps": [{"id": "a", "retries": retries, "timeout_seconds": 60}]}
        )
        assert not report["ready_for_human_review"]


def test_workflow_large_cycle_is_bounded_without_python_recursion():
    steps = [
        {
            "id": f"step{i}",
            "timeout_seconds": 10,
            "depends_on": [f"step{i-1}"] if i else [],
        }
        for i in range(1200)
    ]
    steps[0]["depends_on"] = ["step1199"]
    result = assess_workflow_static({"steps": steps})
    assert not result["ready_for_human_review"]
    assert "workflow_size_limit" in result["blockers"]


def test_valid_workflow_remains_non_executing_and_reviewable():
    report = assess_workflow_static(
        {
            "steps": [
                {"id": "a", "retries": 1, "timeout_seconds": 60},
                {
                    "id": "b",
                    "retries": 0,
                    "timeout_seconds": 60,
                    "depends_on": ("a",),
                },
            ]
        }
    )
    assert report == {
        "blockers": [],
        "ready_for_human_review": True,
        "executed": False,
    }
