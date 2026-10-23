"""A source archive must not mix committed inventory with dirty file bytes."""

import json
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

    def test_untracked_dotenv_cannot_enter_release(self):
        (self.root / ".env").write_text("API_KEY=secret\n")
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertNotIn("trading-gym-1.2.3/.env", archive.namelist())


if __name__ == "__main__":
    unittest.main()
