import importlib.util
from itertools import product
from pathlib import Path
import sys
import unittest

import yaml

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
                "name": name,
                "run_id": 800,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "success",
                "runner_name": "runner-1",
                "steps": [
                    {"name": step, "status": "completed", "conclusion": "success"}
                    for step in receipt.REQUIRED_CI_PROOF_STEPS[name]
                ],
            }
            for name in receipt.REQUIRED_CI_JOBS
        ]
        jobs.append(
            {
                "head_sha": SHA,
                "name": "Dependency Scan (weekly, informational)",
                "run_id": 800,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "skipped",
                "runner_name": None,
                "steps": None,
            }
        )
        result = receipt.classify_jobs(
            {"jobs": jobs}, SHA, expected_run_id=800
        )
        self.assertTrue(result["all_executed_successfully"])
        self.assertTrue(result["complete_job_inventory"])
        self.assertEqual(
            result["jobs"][-1]["evidence"], "intentional_non_gating_skip"
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


class ReceiptCompletenessTests(unittest.TestCase):
    def successful(self, name):
        return {
            "head_sha": SHA,
            "name": name,
            "run_id": 800,
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "success",
            "runner_name": "hosted-runner",
            "steps": [
                {"name": step, "status": "completed", "conclusion": "success"}
                for step in receipt.REQUIRED_CI_PROOF_STEPS[name]
            ],
        }

    def all_required(self):
        return [self.successful(name) for name in sorted(receipt.REQUIRED_CI_JOBS)]

    def test_partial_successful_page_does_not_certify_workflow(self):
        result = receipt.classify_jobs(
            {"jobs": [self.successful("All Checks Passed")]}, SHA
        )
        self.assertFalse(result["all_executed_successfully"])
        self.assertFalse(result["complete_job_inventory"])
        self.assertIn("Lint & Format", result["missing_required_jobs"])

    def test_missing_matrix_cell_is_reported(self):
        jobs = [
            job
            for job in self.all_required()
            if job["name"] != "Tests (windows-latest, 3.12)"
        ]
        result = receipt.classify_jobs({"jobs": jobs}, SHA)
        self.assertFalse(result["all_executed_successfully"])
        self.assertEqual(
            result["missing_required_jobs"], ["Tests (windows-latest, 3.12)"]
        )

    def test_duplicate_job_fails_closed(self):
        jobs = self.all_required() + [self.successful("All Checks Passed")]
        result = receipt.classify_jobs({"jobs": jobs}, SHA)
        self.assertFalse(result["all_executed_successfully"])
        self.assertEqual(result["duplicate_job_names"], ["All Checks Passed"])

    def test_new_unrecognized_job_fails_closed(self):
        jobs = self.all_required() + [
            self.successful("Previously unknown required security gate")
        ]
        result = receipt.classify_jobs({"jobs": jobs}, SHA)
        self.assertFalse(result["all_executed_successfully"])
        self.assertEqual(
            result["unexpected_job_names"],
            ["Previously unknown required security gate"],
        )

    def test_cannot_combine_successes_from_two_different_workflow_runs(self):
        jobs = self.all_required()
        jobs[0]["run_id"] = 801
        result = receipt.classify_jobs({"jobs": jobs}, SHA)
        self.assertFalse(result["all_executed_successfully"])
        self.assertTrue(result["complete_job_inventory"])
        self.assertFalse(result["single_run_identity"])
        self.assertEqual(result["observed_run_ids"], [800, 801])

    def test_missing_run_identity_makes_green_evidence_unqualified(self):
        jobs = self.all_required()
        jobs[0].pop("run_id")
        result = receipt.classify_jobs({"jobs": jobs}, SHA)
        self.assertFalse(result["all_executed_successfully"])
        self.assertEqual(result["unbound_job_names"], [jobs[0]["name"]])

    def test_boolean_or_zero_run_identity_is_not_accepted(self):
        for invalid in (True, 0, "800"):
            with self.subTest(invalid=invalid):
                jobs = self.all_required()
                jobs[0]["run_id"] = invalid
                result = receipt.classify_jobs({"jobs": jobs}, SHA)
                self.assertFalse(result["single_run_identity"])
                self.assertFalse(result["all_executed_successfully"])

    def test_partial_rerun_same_run_id_may_have_multiple_attempts(self):
        jobs = self.all_required()
        jobs[0]["run_attempt"] = 2
        result = receipt.classify_jobs(
            {"jobs": jobs}, SHA, expected_run_id=800
        )
        self.assertTrue(result["single_run_identity"])
        self.assertTrue(result["all_executed_successfully"])
        self.assertEqual(result["observed_run_ids"], [800])

    def test_optional_scan_from_another_run_does_not_qualify(self):
        jobs = self.all_required()
        jobs.append({
            "head_sha": SHA,
            "name": "Dependency Scan (weekly, informational)",
            "run_id": 801,
            "status": "completed",
            "conclusion": "skipped",
        })
        result = receipt.classify_jobs({"jobs": jobs}, SHA)
        self.assertFalse(result["all_executed_successfully"])
        self.assertFalse(result["single_run_identity"])

    def test_complete_jobs_from_wrong_run_cannot_qualify(self):
        jobs = self.all_required()
        result = receipt.classify_jobs(
            {"jobs": jobs}, SHA, expected_run_id=801
        )
        self.assertTrue(result["single_run_identity"])
        self.assertFalse(result["bound_to_expected_run_id"])
        self.assertFalse(result["all_executed_successfully"])
        self.assertEqual(result["expected_run_id"], 801)
        self.assertEqual(result["observed_run_ids"], [800])

    def test_missing_expected_run_id_never_qualifies(self):
        result = receipt.classify_jobs({"jobs": self.all_required()}, SHA)
        self.assertTrue(result["single_run_identity"])
        self.assertFalse(result["bound_to_expected_run_id"])
        self.assertFalse(result["all_executed_successfully"])

    def test_invalid_expected_run_id_is_rejected(self):
        for invalid in (0, -1, True, "800", 800.0):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    receipt.classify_jobs(
                        {"jobs": self.all_required()},
                        SHA,
                        expected_run_id=invalid,
                    )

    def test_jobs_cli_rejects_missing_run_id_before_git_access(self):
        with self.assertRaises(SystemExit) as failure:
            receipt.main(
                [
                    "--source-sha",
                    SHA,
                    "--output",
                    "/tmp/unused-receipt.json",
                    "--scope",
                    "ci",
                    "--jobs-json",
                    "/tmp/unused-jobs.json",
                ]
            )
        self.assertEqual(failure.exception.code, 2)

    def test_cli_run_id_only_valid_for_jobs_mode(self):
        with self.assertRaises(SystemExit) as failure:
            receipt.main(
                [
                    "--source-sha",
                    SHA,
                    "--output",
                    "/tmp/unused-receipt.json",
                    "--scope",
                    "command",
                    "--run-id",
                    "800",
                    "--",
                    "true",
                ]
            )
        self.assertEqual(failure.exception.code, 2)

    def test_missing_or_skipped_named_gate_fails_green_job(self):
        for missing, status, conclusion in (
            (True, None, None),
            (False, "completed", "skipped"),
            (False, "in_progress", None),
            (False, "completed", "failure"),
        ):
            with self.subTest(missing=missing, status=status, conclusion=conclusion):
                jobs = self.all_required()
                frontend = next(job for job in jobs if job["name"] == "Frontend Tests")
                if missing:
                    frontend["steps"] = [
                        {
                            "name": "Set up Node.js",
                            "status": "completed",
                            "conclusion": "success",
                        }
                    ]
                else:
                    frontend["steps"][0].update(
                        {"status": status, "conclusion": conclusion}
                    )
                result = receipt.classify_jobs(
                    {"jobs": jobs}, SHA, expected_run_id=800
                )
                self.assertFalse(result["all_executed_successfully"])
                observed = next(
                    job for job in result["jobs"] if job["name"] == "Frontend Tests"
                )
                self.assertTrue(observed["missing_or_unsuccessful_gate_steps"])

    def test_duplicate_named_gate_fails_green_job(self):
        jobs = self.all_required()
        frontend = next(job for job in jobs if job["name"] == "Frontend Tests")
        frontend["steps"].append(
            {"name": "Run tests", "status": "completed", "conclusion": "skipped"}
        )
        result = receipt.classify_jobs(
            {"jobs": jobs}, SHA, expected_run_id=800
        )
        self.assertFalse(result["all_executed_successfully"])

    def test_real_named_gate_steps_allow_partial_rerun(self):
        jobs = self.all_required()
        jobs[0]["run_attempt"] = 2
        result = receipt.classify_jobs(
            {"jobs": jobs}, SHA, expected_run_id=800
        )
        self.assertTrue(result["complete_verification_step_inventory"])
        self.assertTrue(result["all_executed_successfully"])

    def test_proof_step_inventory_matches_actual_ci_workflow(self):
        workflow = yaml.safe_load(
            (Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(receipt.REQUIRED_CI_PROOF_STEPS), receipt.REQUIRED_CI_JOBS
        )
        workflow_step_names = {
            job["name"]: [
                step.get("name") for step in job["steps"] if "name" in step
            ]
            for job in workflow["jobs"].values()
        }
        for job_name, steps in receipt.REQUIRED_CI_PROOF_STEPS.items():
            workflow_job = "Tests" if job_name.startswith("Tests (") else job_name
            for step in steps:
                self.assertEqual(
                    workflow_step_names[workflow_job].count(step),
                    1,
                    (job_name, step),
                )

    def test_queued_job_with_success_label_does_not_qualify(self):
        jobs = self.all_required()
        jobs[0]["status"] = "queued"
        result = receipt.classify_jobs({"jobs": jobs}, SHA)
        self.assertFalse(result["all_executed_successfully"])

    def test_required_names_match_actual_workflow_matrix(self):
        workflow = yaml.safe_load(
            (Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml")
            .read_text(encoding="utf-8")
        )
        observed = set()
        for definition in workflow["jobs"].values():
            name = definition["name"]
            matrix = definition.get("strategy", {}).get("matrix")
            if matrix:
                for os_name, version in product(
                    matrix["os"], matrix["python-version"]
                ):
                    observed.add(f"{name} ({os_name}, {version})")
            else:
                observed.add(name)
        self.assertEqual(
            receipt.REQUIRED_CI_JOBS,
            observed - receipt.OPTIONAL_SKIPPED_JOBS,
        )


if __name__ == "__main__":
    unittest.main()
