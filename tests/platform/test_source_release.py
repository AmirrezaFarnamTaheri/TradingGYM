from __future__ import annotations

from pathlib import Path
import subprocess
import json
import sys
import zipfile


def test_source_release_builder_emits_safe_complete_archive(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    out = tmp_path / "release.zip"
    subprocess.run(
        [sys.executable, str(root / "scripts/build_source_release.py"), str(out)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert out.is_file()
    version = json.loads(
        (root / "trading_gym/release_manifest.json").read_text(encoding="utf-8")
    )["version"]
    expected_root = f"trading-gym-{version}/"
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
        assert names and all(name.startswith(expected_root) for name in names)
        assert any(name.endswith("trading_gym/release_manifest.json") for name in names)
        assert any(name.endswith("axiomforge/decision_lifecycle.py") for name in names)
        assert not any(
            "/__pycache__/" in name or "/.pytest_cache/" in name for name in names
        )
        assert all(
            not Path(name).is_absolute() and ".." not in Path(name).parts
            for name in names
        )
        assert archive.testzip() is None
        assert not any(
            name.startswith(expected_root + "trading_gym/data/manifests/")
            and name.endswith(".lock")
            for name in names
        )
        assert not any(
            name.startswith(expected_root + "rust/axiom_kernel/")
            and name.endswith(".exit")
            for name in names
        )
