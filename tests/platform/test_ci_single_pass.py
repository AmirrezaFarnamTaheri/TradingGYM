from pathlib import Path
import yaml


def test_ci_matrix_runs_the_real_dependency_suite_once():
    root = Path(__file__).resolve().parents[2]
    document = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    job = document["jobs"]["python"]
    assert job["strategy"]["matrix"]["os"] == ["ubuntu-latest", "windows-latest"]
    assert set(job["strategy"]["matrix"]["python"]) == {"3.11", "3.12"}
    steps = [s for s in job["steps"] if "pytest " in s.get("run", "")]
    assert len(steps) == 1
    assert '-m "not integration"' in steps[0]["run"]
    assert steps[0]["env"]["TRADING_GYM_REQUIRE_REAL_DEPS"] == "1"
