"""Guard single-pass unit tests and coverage on the mixed-OS CI matrix.

The Ubuntu 3.12 cell produces the coverage artifact; all other supported
cells execute the same non-integration suite without coverage. GitHub
job-level expressions are independent of each OS's default shell.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
PLAIN = "matrix.os != 'ubuntu-latest' || matrix.python-version != '3.12'"
COVERAGE = "matrix.os == 'ubuntu-latest' && matrix.python-version == '3.12'"


def _test_job() -> dict:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"]["test"]


def test_ci_test_matrix_has_exclusive_test_steps() -> None:
    job = _test_job()
    assert job["strategy"]["matrix"]["os"] == [
        "ubuntu-latest",
        "windows-latest",
    ]
    assert set(job["strategy"]["matrix"]["python-version"]) == {"3.11", "3.12"}
    steps = job["steps"]
    ordinary = [s for s in steps if s.get("name") == "Run unit tests"]
    covered = [s for s in steps if s.get("name") == "Run unit tests with coverage"]
    assert len(ordinary) == len(covered) == 1
    assert ordinary[0]["if"] == PLAIN
    assert covered[0]["if"] == COVERAGE
    for step in (ordinary[0], covered[0]):
        assert 'pytest -m "not integration"' in step["run"]
        assert step["env"]["PYTHONPATH"] == "."
        assert "[[" not in step["run"]
        assert "$env:" not in step["run"]
    assert "--cov=trading_gym" not in ordinary[0]["run"]
    assert "--cov=trading_gym" in covered[0]["run"]
    assert "--cov=apps" in covered[0]["run"]
    assert "--cov-report=xml" in covered[0]["run"]


@pytest.mark.parametrize("os", ["ubuntu-latest", "windows-latest"])
@pytest.mark.parametrize("version", ["3.11", "3.12"])
def test_exactly_one_unit_suite_and_at_most_one_coverage_producer(
    os: str, version: str
) -> None:
    covered = os == "ubuntu-latest" and version == "3.12"
    plain = os != "ubuntu-latest" or version != "3.12"
    assert plain != covered
    assert int(plain) + int(covered) == 1


def test_coverage_upload_runs_only_in_coverage_matrix_cell() -> None:
    steps = _test_job()["steps"]
    upload = [s for s in steps if s.get("name") == "Upload coverage reports"]
    assert len(upload) == 1
    assert upload[0]["if"] == COVERAGE
    assert upload[0]["with"]["files"] == "./coverage.xml"
