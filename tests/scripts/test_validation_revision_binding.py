"""Reject stale, dirty, or changed source identity for validation receipts."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parents[2] / "scripts" / "validation_receipt.py"
spec = importlib.util.spec_from_file_location("validated_receipt", SOURCE)
receipt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receipt)


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


class ReceiptRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "fixture@example.invalid")
        git(self.root, "config", "user.name", "Fixture")
        (self.root / "tracked.txt").write_text("v1\n", encoding="utf-8")
        git(self.root, "add", "tracked.txt")
        git(self.root, "commit", "-qm", "fixture")
        self.sha = git(self.root, "rev-parse", "HEAD")

    def identity(self, sha):
        with patch.object(receipt, "ROOT", self.root):
            return receipt.source_identity(sha)

    def test_accepts_clean_matching_revision(self):
        admitted = self.identity(self.sha)
        self.assertEqual(admitted["observed_head_sha"], self.sha)
        self.assertEqual(admitted["working_tree_status"], [])

    def test_rejects_wrong_declared_commit(self):
        with self.assertRaisesRegex(ValueError, "match"):
            self.identity("a" * 40 if self.sha != "a" * 40 else "b" * 40)

    def test_rejects_unstaged_tracked_edits(self):
        (self.root / "tracked.txt").write_text("v2\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.identity(self.sha)

    def test_rejects_staged_tracked_edits(self):
        (self.root / "tracked.txt").write_text("v2\n", encoding="utf-8")
        git(self.root, "add", "tracked.txt")
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.identity(self.sha)

    def test_rejects_untracked_source_files(self):
        (self.root / "not-reviewed.py").write_text("print('new')\n")
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.identity(self.sha)

    def test_rejects_changes_during_the_validation_command(self):
        admitted = self.identity(self.sha)
        (self.root / "tracked.txt").write_text("modified\n")
        with patch.object(receipt, "ROOT", self.root):
            with self.assertRaisesRegex(ValueError, "dirty|changed"):
                receipt.verify_unchanged(self.sha, admitted)

    def test_accepts_unchanged_checkout_after_the_command(self):
        admitted = self.identity(self.sha)
        with patch.object(receipt, "ROOT", self.root):
            receipt.verify_unchanged(self.sha, admitted)


if __name__ == "__main__":
    unittest.main()
