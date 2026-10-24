"""Reject stale, dirty, or changed source identity for validation receipts."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
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


    def test_refuses_output_overwriting_a_tracked_source_file(self):
        original = (self.root / "tracked.txt").read_bytes()
        with patch.object(receipt, "ROOT", self.root):
            with self.assertRaisesRegex(ValueError, "outside"):
                receipt.write_receipt(
                    self.root / "tracked.txt", {"outcome": "passed"}
                )
        self.assertEqual((self.root / "tracked.txt").read_bytes(), original)

    def test_invalid_output_path_rejected_before_running_command(self):
        target = self.root / "receipt.json"
        with patch.object(receipt, "ROOT", self.root):
            with self.assertRaises(SystemExit) as outcome:
                receipt.main(
                    [
                        "--source-sha",
                        self.sha,
                        "--output",
                        str(target),
                        "--scope",
                        "guard",
                        "--",
                        sys.executable,
                        "-c",
                        "print('noop')",
                    ]
                )
        self.assertEqual(outcome.exception.code, 2)
        self.assertFalse(target.exists())
        self.assertEqual(self.identity(self.sha)["observed_head_sha"], self.sha)

    def test_symlink_from_outside_cannot_write_inside_checkout(self):
        with tempfile.TemporaryDirectory() as external:
            link = Path(external) / "alias"
            try:
                link.symlink_to(self.root, target_is_directory=True)
            except OSError:
                self.skipTest("symlinks are unavailable")
            with patch.object(receipt, "ROOT", self.root):
                with self.assertRaisesRegex(ValueError, "outside"):
                    receipt.write_receipt(link / "receipt.json", {"ok": True})
            self.assertFalse((self.root / "receipt.json").exists())

    def test_valid_outside_receipt_records_same_clean_revision(self):
        with tempfile.TemporaryDirectory() as external:
            dest = Path(external) / "receipt.json"
            with patch.object(receipt, "ROOT", self.root):
                code = receipt.main(
                    [
                        "--source-sha",
                        self.sha,
                        "--output",
                        str(dest),
                        "--scope",
                        "external-output",
                        "--",
                        sys.executable,
                        "-c",
                        "print('ok')",
                    ]
                )
            self.assertEqual(code, 0)
            document = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(document["source"]["observed_head_sha"], self.sha)
            self.assertEqual(document["execution"]["outcome"], "passed")
            self.assertEqual(self.identity(self.sha)["observed_head_sha"], self.sha)


if __name__ == "__main__":
    unittest.main()
