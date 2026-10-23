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


def _isolated_source_checkout(tmp_path: Path) -> Path:
    """Create a disposable Git index, never modifying the real project."""
    import shutil

    root = Path(__file__).resolve().parents[2]
    project = tmp_path / "source"
    (project / "scripts").mkdir(parents=True)
    (project / "trading_gym").mkdir()
    shutil.copy2(
        root / "scripts" / "build_source_release.py",
        project / "scripts" / "build_source_release.py",
    )
    (project / "trading_gym" / "release_manifest.json").write_text(
        json.dumps({"version": "1.0.0"}), encoding="utf-8"
    )
    (project / "README.md").write_text("tracked source", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "add",
            "README.md",
            "scripts/build_source_release.py",
            "trading_gym/release_manifest.json",
        ],
        check=True,
    )
    # The source builder intentionally rejects staged, uncommitted inventory.
    # Keep each test checkout at a real clean Git revision.
    for field, value in (
        ("user.email", "fixture@example.invalid"),
        ("user.name", "Fixture"),
    ):
        subprocess.run(
            ["git", "-C", str(project), "config", field, value], check=True
        )
    subprocess.run(
        ["git", "-C", str(project), "commit", "-qm", "baseline fixture"],
        check=True,
    )
    return project


def test_source_release_excludes_untracked_secrets(tmp_path: Path) -> None:
    project = _isolated_source_checkout(tmp_path)
    (project / ".env").write_text("PRIVATE_API_KEY=secret", encoding="utf-8")
    (project / "wallet-secret.pem").write_text("private key", encoding="utf-8")
    (project / "scratch.txt").write_text("local scratch", encoding="utf-8")
    destination = tmp_path / "source.zip"

    subprocess.run(
        [
            sys.executable,
            str(project / "scripts" / "build_source_release.py"),
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
        assert any(name.endswith("/README.md") for name in names)
        assert not any(name.endswith("/.env") for name in names)
        assert not any(name.endswith("/wallet-secret.pem") for name in names)
        assert not any(name.endswith("/scratch.txt") for name in names)
        assert not any(b"PRIVATE_API_KEY" in archive.read(name) for name in names)


def test_source_release_rejects_external_tracked_symlink(tmp_path: Path) -> None:
    import pytest

    project = _isolated_source_checkout(tmp_path)
    secret = tmp_path / "outside-private-key.pem"
    secret.write_text("private key", encoding="utf-8")
    try:
        (project / "leak.pem").symlink_to(secret)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable on this platform")
    subprocess.run(["git", "-C", str(project), "add", "leak.pem"], check=True)
    subprocess.run(
        ["git", "-C", str(project), "commit", "-qm", "tracked unsafe symlink"],
        check=True,
    )
    destination = tmp_path / "should-not-exist.zip"
    result = subprocess.run(
        [
            sys.executable,
            str(project / "scripts" / "build_source_release.py"),
            str(destination),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert not destination.exists()


def test_source_release_requires_git_index(tmp_path: Path) -> None:
    import shutil

    project = _isolated_source_checkout(tmp_path)
    shutil.rmtree(project / ".git")
    (project / ".env").write_text("PRIVATE_API_KEY=secret", encoding="utf-8")
    destination = tmp_path / "no-index.zip"
    result = subprocess.run(
        [
            sys.executable,
            str(project / "scripts" / "build_source_release.py"),
            str(destination),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Git" in result.stderr
    assert not destination.exists()


def test_source_release_rejects_tracked_dotenv_secrets(tmp_path: Path) -> None:
    project = _isolated_source_checkout(tmp_path)
    (project / ".env").write_text("SECRET=unsafe", encoding="utf-8")
    subprocess.run(["git", "-C", str(project), "add", "-f", ".env"], check=True)
    subprocess.run(
        ["git", "-C", str(project), "commit", "-qm", "tracked unsafe dotenv"],
        check=True,
    )
    destination = tmp_path / "tracked-secret.zip"
    result = subprocess.run(
        [
            sys.executable,
            str(project / "scripts" / "build_source_release.py"),
            str(destination),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "dotenv" in result.stderr
    assert not destination.exists()
