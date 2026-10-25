from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_documented_runtime_modules_exist():
    for module in [
        "agent/daemon.py",
        "agent/fusion.py",
        "agent/strategy_planner.py",
        "agent/shadow_learning.py",
        "api/main.py",
        "cli.py",
    ]:
        assert (ROOT / "trading_gym" / module).is_file()


def test_readme_describes_local_paper_boundary():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Funded execution is disabled by default" in text
    assert "GitHub Actions" in text
