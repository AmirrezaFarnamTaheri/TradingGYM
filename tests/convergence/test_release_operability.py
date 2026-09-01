from pathlib import Path
import re


def test_release_workflow_and_full_suite_are_blocking_and_sha_pinned() -> None:
    root = Path(__file__).resolve().parents[2]
    release = (root / ".github/workflows/release.yml").read_text()
    suite = root / "scripts/run_full_suite.sh"
    assert suite.exists()
    assert "scripts/run_full_suite.sh" in release
    for line in release.splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40}(?:\s|$)", line.split("#", 1)[0].rstrip())
    script = suite.read_text()
    for gate in [
        "verify_release_manifest.py",
        "verify_workflow_pins.py",
        "verify_release_permissions.py",
        "export_openapi.py --check",
        "audit_module_accountability.py --check",
        "audit_recovered_target_coverage.py --check",
        "python -m pytest -q",
    ]:
        assert gate in script
