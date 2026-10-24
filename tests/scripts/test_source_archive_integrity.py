"""A source archive must not mix committed inventory with dirty file bytes."""

import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

SOURCE = Path(__file__).parents[2] / "scripts" / "build_source_release.py"


class ReleaseArchiveIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "checkout"
        self.root.mkdir()
        (self.root / "scripts").mkdir()
        (self.root / "trading_gym").mkdir()
        (self.root / "scripts" / "build_source_release.py").write_bytes(
            SOURCE.read_bytes()
        )
        (self.root / "trading_gym" / "release_manifest.json").write_text(
            json.dumps({"version": "1.2.3"}), encoding="utf-8"
        )
        (self.root / "payload.txt").write_text("committed source\n", encoding="utf-8")
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.archive = Path(self.temporary.name) / "release.zip"

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True
        )

    def build(self):
        return subprocess.run(
            [
                sys.executable,
                str(self.root / "scripts" / "build_source_release.py"),
                str(self.archive),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )

    def test_clean_checkout_archives_committed_bytes(self):
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(
                archive.read("trading-gym-1.2.3/payload.txt"), b"committed source\n"
            )

    def test_unstaged_changes_cannot_enter_release(self):
        (self.root / "payload.txt").write_text("uncommitted secret\n")
        result = self.build()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.archive.exists())

    def test_staged_changes_cannot_enter_release(self):
        (self.root / "payload.txt").write_text("staged secret\n")
        self.git("add", "payload.txt")
        result = self.build()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.archive.exists())


    def test_missing_git_checkout_reports_actionable_git_error(self):
        shutil.rmtree(self.root / ".git")
        failure = self.build()
        self.assertNotEqual(failure.returncode, 0)
        self.assertIn("Git", failure.stderr)
        self.assertFalse(self.archive.exists())

    def test_untracked_dotenv_cannot_enter_release(self):
        (self.root / ".env").write_text("API_KEY=secret\n")
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertNotIn("trading-gym-1.2.3/.env", archive.namelist())


    def test_archive_uses_git_blob_when_worktree_read_is_raced(self):
        # A deterministic file-reader substitution simulates a path swap after
        # the clean/index preflight. Git still sees a clean working tree.
        wrapper = """
from pathlib import Path
from io import BytesIO
import runpy
import sys
_original_open = Path.open
def swapped_read(self, *args, **kwargs):
    if self.name == "payload.txt" and args and args[0] == "rb":
        return BytesIO(b"transient tampered data\\n")
    return _original_open(self, *args, **kwargs)
Path.open = swapped_read
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                wrapper,
                str(self.root / "scripts" / "build_source_release.py"),
                str(self.archive),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(
                archive.read("trading-gym-1.2.3/payload.txt"),
                b"committed source\n",
            )

    def test_blob_read_failure_keeps_last_valid_release_untouched(self):
        clean = self.build()
        self.assertEqual(clean.returncode, 0, clean.stderr)
        previous = self.archive.read_bytes()
        wrapper = """
import subprocess
import runpy
import sys
_original_popen = subprocess.Popen
def fail_source_blob(command, *args, **kwargs):
    if isinstance(command, list) and "cat-file" in command:
        raise OSError("simulated Git object access failure")
    return _original_popen(command, *args, **kwargs)
subprocess.Popen = fail_source_blob
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
        failure = subprocess.run(
            [
                sys.executable,
                "-c",
                wrapper,
                str(self.root / "scripts" / "build_source_release.py"),
                str(self.archive),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(failure.returncode, 0)
        self.assertEqual(self.archive.read_bytes(), previous)
        self.assertEqual(list(Path(self.temporary.name).glob(".source-*.zip")), [])

    def test_repeated_archives_are_deterministic(self):
        first = self.build()
        self.assertEqual(first.returncode, 0, first.stderr)
        initial = self.archive.read_bytes()
        second = self.build()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(initial, self.archive.read_bytes())

    def test_archive_root_version_is_taken_from_committed_manifest(self):
        # A transient worktree read must not let the release's folder name
        # diverge from the committed source manifest.
        wrapper = """
from pathlib import Path
import runpy
import sys
_original_read = Path.read_text
def spoofed_manifest(self, *args, **kwargs):
    if self.name == "release_manifest.json":
        return '{"version":"9.9.9-fake"}'
    return _original_read(self, *args, **kwargs)
Path.read_text = spoofed_manifest
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                wrapper,
                str(self.root / "scripts" / "build_source_release.py"),
                str(self.archive),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertIn("trading-gym-1.2.3/payload.txt", archive.namelist())
            self.assertNotIn("trading-gym-9.9.9-fake/payload.txt", archive.namelist())

    def test_release_root_rejects_path_traversal_version(self):
        (self.root / "trading_gym" / "release_manifest.json").write_text(
            json.dumps({"version": "../../escaped"}), encoding="utf-8"
        )
        self.git("add", "trading_gym/release_manifest.json")
        self.git("commit", "-qm", "malicious version fixture")
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.archive.exists())


if __name__ == "__main__":
    unittest.main()
