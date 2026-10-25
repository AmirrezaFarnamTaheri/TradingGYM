"""Smoke tests for the skill-intake plane (evidence-only by design).

Pins the fail-closed guarantees of skill-package intake, quarantine, and static
assurance: packages are inspected and risk-marked, never activated, and the
quarantine repository enforces reviewer/preparer role separation.
"""

from __future__ import annotations

import pathlib

import pytest

from trading_gym.agent.skills.intake import inspect_skill_package
from trading_gym.agent.skills.quarantine import SkillQuarantineRepository
from trading_gym.agent.skills.static_assurance import (
    assess_skill_static,
    assess_workflow_static,
)


@pytest.fixture
def benign_package(tmp_path: pathlib.Path) -> pathlib.Path:
    pkg = tmp_path / "benign_skill.json"
    pkg.write_text('{"name": "benign", "capabilities": ["read"]}', encoding="utf-8")
    return pkg


@pytest.fixture
def risky_package(tmp_path: pathlib.Path) -> pathlib.Path:
    pkg = tmp_path / "risky_skill.json"
    pkg.write_text('subprocess call: os.system("rm -rf")', encoding="utf-8")
    return pkg


class TestSkillIntake:
    def test_benign_package_is_hashed_and_quarantined(
        self, benign_package: pathlib.Path
    ) -> None:
        record = inspect_skill_package(benign_package)
        assert record["quarantined"] is True
        assert record["activated"] is False
        assert len(record["sha256"]) == 64

    def test_risky_markers_are_detected(self, risky_package: pathlib.Path) -> None:
        record = inspect_skill_package(risky_package)
        assert "subprocess" in record["risk_markers"]
        assert record["quarantined"] is True


class TestStaticAssurance:
    def test_valid_skill_passes_without_activation(self) -> None:
        result = assess_skill_static({"sha256": "a" * 64, "capabilities": ("read",)})
        assert result["blockers"] == []
        assert result["ready_for_human_review"] is True
        assert result["executed"] is False

    def test_missing_hash_blocks_review(self) -> None:
        result = assess_skill_static({"sha256": "short", "capabilities": ()})
        assert "missing_hash" in result["blockers"]
        assert result["ready_for_human_review"] is False

    def test_forbidden_capabilities_block_review(self) -> None:
        result = assess_skill_static(
            {"sha256": "a" * 64, "capabilities": ("trade", "read")}
        )
        assert "forbidden_capabilities" in result["blockers"]

    def test_valid_workflow_passes(self) -> None:
        result = assess_workflow_static(
            {
                "steps": (
                    {"id": "a", "retries": 1, "timeout_seconds": 60, "depends_on": ()},
                    {
                        "id": "b",
                        "retries": 1,
                        "timeout_seconds": 60,
                        "depends_on": ("a",),
                    },
                )
            }
        )
        assert result["blockers"] == []
        assert result["executed"] is False

    def test_cyclic_dependency_is_detected(self) -> None:
        result = assess_workflow_static(
            {
                "steps": (
                    {
                        "id": "a",
                        "retries": 1,
                        "timeout_seconds": 60,
                        "depends_on": ("b",),
                    },
                    {
                        "id": "b",
                        "retries": 1,
                        "timeout_seconds": 60,
                        "depends_on": ("a",),
                    },
                )
            }
        )
        assert "cycle" in result["blockers"]

    def test_unknown_dependency_is_detected(self) -> None:
        result = assess_workflow_static(
            {
                "steps": (
                    {
                        "id": "a",
                        "retries": 1,
                        "timeout_seconds": 60,
                        "depends_on": ("nonexistent",),
                    },
                )
            }
        )
        assert "unknown_dependency" in result["blockers"]

    def test_unbounded_retries_are_detected(self) -> None:
        result = assess_workflow_static(
            {"steps": ({"id": "a", "retries": 99, "timeout_seconds": 60},)}
        )
        assert "unbounded_retries" in result["blockers"]


class TestQuarantineRepository:
    def test_benign_record_is_admitted_unactivated(self) -> None:
        repo = SkillQuarantineRepository()
        record = repo.admit(
            "skill_a", {"sha256": "a" * 64}, reviewer="alice", preparer="bob"
        )
        assert record["activated"] is False
        assert "skill_a" in repo.list()

    def test_role_separation_is_enforced(self) -> None:
        repo = SkillQuarantineRepository()
        with pytest.raises(ValueError, match="role separation"):
            repo.admit("skill_a", {}, reviewer="alice", preparer="alice")

    def test_risky_record_is_refused(self) -> None:
        repo = SkillQuarantineRepository()
        with pytest.raises(ValueError, match="risky package"):
            repo.admit(
                "skill_a",
                {"risk_markers": ["subprocess"]},
                reviewer="alice",
                preparer="bob",
            )
