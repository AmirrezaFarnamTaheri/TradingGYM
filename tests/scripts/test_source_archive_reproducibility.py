"""Release bytes must depend on Git content and modes, not checkout timestamps."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

SOURCE = Path(__file__).parents[2] / "scripts" / "build_source_release.py"


class ReproducibleArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "trading_gym").mkdir()
        (self.root / "scripts" / "build_source_release.py").write_bytes(
            SOURCE.read_bytes()
        )
        (self.root / "trading_gym" / "release_manifest.json").write_text(
            json.dumps({"version": "1.2.3"}), encoding="utf-8"
        )
        self.payload = self.root / "payload.txt"
        self.payload.write_text("source-controlled data\n", encoding="utf-8")
        self.executable = self.root / "launch.sh"
        self.executable.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        self.executable.chmod(0o755)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("add", ".")
        # Git index modes are authoritative even on Windows checkouts.
        self.git("update-index", "--chmod=+x", "launch.sh")
        self.git("commit", "-qm", "fixture")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True
        )

    def build(self, suffix):
        archive = Path(self.temporary.name) / f"{suffix}.zip"
        result = subprocess.run(
            [
                sys.executable,
                str(self.root / "scripts" / "build_source_release.py"),
                str(archive),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return archive

    def test_identical_commit_same_zip_after_changing_file_timestamps(self):
        before = self.build("before").read_bytes()
        os.utime(self.payload, (1577836800, 1577836800))
        self.assertEqual(self.git("status", "--porcelain").stdout, b"")
        self.assertEqual(before, self.build("after").read_bytes())

    def test_zip_preserves_committed_executable_bit_not_checkout_mode(self):
        before = self.build("before")
        with zipfile.ZipFile(before) as archive:
            mode = archive.getinfo("trading-gym-1.2.3/launch.sh").external_attr >> 16
            self.assertEqual(mode & 0o111, 0o111)

        # Simulate Windows-style checkouts where filesystem mode is not tracked.
        self.git("config", "core.filemode", "false")
        self.executable.chmod(0o644)
        self.assertEqual(self.git("status", "--porcelain").stdout, b"")
        after = self.build("after")
        with zipfile.ZipFile(after) as archive:
            mode = archive.getinfo("trading-gym-1.2.3/launch.sh").external_attr >> 16
            self.assertEqual(mode & 0o111, 0o111)
        self.assertEqual(before.read_bytes(), after.read_bytes())


if __name__ == "__main__":
    unittest.main()
