import importlib.util
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location(
    "receipt", Path(__file__).parents[2] / "scripts/validation_receipt.py"
)
receipt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receipt)
SHA = "a" * 40


class ReceiptTests(unittest.TestCase):
    def test_failed_unassigned_job_is_not_reported_as_a_test_failure(self):
        result = receipt.classify_jobs(
            {
                "jobs": [
                    {
                        "head_sha": SHA,
                        "status": "completed",
                        "conclusion": "failure",
                        "steps": [],
                        "runner_name": "",
                    }
                ]
            },
            SHA,
        )
        self.assertEqual(result["jobs"][0]["evidence"], "no_execution_evidence")
        self.assertEqual(result["admission_cause"], "unverified")
        self.assertFalse(result["all_executed_successfully"])

    def test_success_label_without_execution_never_qualifies(self):
        result = receipt.classify_jobs(
            {
                "jobs": [
                    {
                        "head_sha": SHA,
                        "status": "completed",
                        "conclusion": "success",
                        "steps": [],
                    }
                ]
            },
            SHA,
        )
        self.assertFalse(result["all_executed_successfully"])
        with self.assertRaises(ValueError):
            receipt.classify_jobs({"jobs": [{"head_sha": "b" * 40}]}, SHA)

    def test_returncode_and_output_digest_are_from_actual_command(self):
        result = receipt.execute(
            [sys.executable, "-c", "print('fixture'); raise SystemExit(7)"], 5
        )
        self.assertEqual(result["returncode"], 7)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["output_bytes"], 8)

    def test_null_or_missing_step_metadata_is_not_execution_evidence(self):
        for optional_fields in ({}, {"steps": None}):
            with self.subTest(optional_fields=optional_fields):
                job = {
                    "head_sha": SHA,
                    "name": "Bare hosted runner",
                    "status": "completed",
                    "conclusion": "failure",
                    "runner_name": None,
                    **optional_fields,
                }
                result = receipt.classify_jobs({"jobs": [job]}, SHA)
                self.assertEqual(
                    result["jobs"][0]["evidence"], "no_execution_evidence"
                )
                self.assertFalse(result["all_executed_successfully"])

    def test_queued_or_runner_assigned_without_steps_is_not_proven_blocked(self):
        cases = (
            {"status": "queued", "conclusion": None, "runner_name": None},
            {"status": "completed", "conclusion": "failure", "runner_name": "hosted-1"},
        )
        for case in cases:
            with self.subTest(case=case):
                result = receipt.classify_jobs(
                    {"jobs": [{"head_sha": SHA, "steps": None, **case}]},
                    SHA,
                )
                self.assertEqual(
                    result["jobs"][0]["evidence"],
                    "incomplete_metadata_or_pending",
                )


class ReceiptOptionalSkipTests(unittest.TestCase):
    def test_non_gating_weekly_scan_does_not_make_green_pr_fail(self):
        jobs = [
            {
                "head_sha": SHA,
                "name": "All Checks Passed",
                "status": "completed",
                "conclusion": "success",
                "runner_name": "runner-1",
                "steps": [{"name": "verify", "conclusion": "success"}],
            },
            {
                "head_sha": SHA,
                "name": "Dependency Scan (weekly, informational)",
                "status": "completed",
                "conclusion": "skipped",
                "runner_name": None,
                "steps": None,
            },
        ]
        result = receipt.classify_jobs({"jobs": jobs}, SHA)
        self.assertTrue(result["all_executed_successfully"])
        self.assertEqual(
            result["jobs"][1]["evidence"], "intentional_non_gating_skip"
        )

    def test_required_skipped_job_still_blocks_qualification(self):
        jobs = [
            {
                "head_sha": SHA,
                "name": "All Checks Passed",
                "status": "completed",
                "conclusion": "success",
                "runner_name": "runner-1",
                "steps": [{"name": "verify"}],
            },
            {
                "head_sha": SHA,
                "name": "Integration Tests",
                "status": "completed",
                "conclusion": "skipped",
                "steps": None,
            },
        ]
        result = receipt.classify_jobs({"jobs": jobs}, SHA)
        self.assertFalse(result["all_executed_successfully"])
        self.assertEqual(result["jobs"][1]["evidence"], "skipped")

    def test_failing_weekly_scan_is_not_exempt(self):
        jobs = [
            {
                "head_sha": SHA,
                "name": "All Checks Passed",
                "status": "completed",
                "conclusion": "success",
                "runner_name": "runner-1",
                "steps": [{"name": "verify"}],
            },
            {
                "head_sha": SHA,
                "name": "Dependency Scan (weekly, informational)",
                "status": "completed",
                "conclusion": "failure",
                "runner_name": "runner-2",
                "steps": [{"name": "scan"}],
            },
        ]
        result = receipt.classify_jobs({"jobs": jobs}, SHA)
        self.assertFalse(result["all_executed_successfully"])

    def test_informational_skip_alone_cannot_prove_ci_executed(self):
        job = {
            "head_sha": SHA,
            "name": "Dependency Scan (weekly, informational)",
            "status": "completed",
            "conclusion": "skipped",
        }
        result = receipt.classify_jobs({"jobs": [job]}, SHA)
        self.assertFalse(result["all_executed_successfully"])

    def test_queued_informational_scan_is_not_a_completed_exception(self):
        jobs = [
            {
                "head_sha": SHA,
                "name": "All Checks Passed",
                "status": "completed",
                "conclusion": "success",
                "runner_name": "runner-1",
                "steps": [{"name": "verify"}],
            },
            {
                "head_sha": SHA,
                "name": "Dependency Scan (weekly, informational)",
                "status": "queued",
                "conclusion": "skipped",
            },
        ]
        result = receipt.classify_jobs({"jobs": jobs}, SHA)
        self.assertFalse(result["all_executed_successfully"])


if __name__ == "__main__":
    unittest.main()
