"""Dependency-free regression tests for the cross-platform task entry point."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class ProjectTasksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location(
            "project_tasks", ROOT / "scripts" / "project.py"
        )
        cls.tasks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tasks)

    def test_train_uses_current_cli_and_forwards_arguments(self) -> None:
        command = self.tasks.build_command("train", ["--dry-run"])
        self.assertEqual(
            command[:5], ("poetry", "run", "python", "-m", "apps.trainer.main")
        )
        self.assertEqual(command[5:], ("--dry-run",))

    def test_paper_never_invents_model_or_live_confirmation(self) -> None:
        command = self.tasks.build_command("paper", [])
        self.assertEqual(command, ("poetry", "run", "python", "-m", "apps.paper.main"))

    def test_paper_forwards_artifact_hash_as_separate_arguments(self) -> None:
        args = ["--model-path", "models/a b.zip", "--model-sha256", "a" * 64]
        self.assertEqual(self.tasks.build_command("paper", args)[5:], tuple(args))

    def test_live_requires_governed_api(self) -> None:
        with self.assertRaisesRegex(ValueError, "authenticated API"):
            self.tasks.build_command("live", ["--live"])

    def test_setup_uses_lockfile_without_global_configuration(self) -> None:
        self.assertEqual(
            self.tasks.build_command("setup", []),
            ("poetry", "install", "--with", "dev"),
        )
        with self.assertRaises(ValueError):
            self.tasks.build_command("setup", ["--no-root"])

    def test_type_gate_includes_memory_and_kernel(self) -> None:
        command = self.tasks.build_command("typecheck", [])
        self.assertIn("trading_gym/agent/persistent_experience.py", command)
        self.assertIn("trading_gym/playground/kernel.py", command)

    def test_test_arguments_remain_literal(self) -> None:
        command = self.tasks.build_command("test", ["-k", "name; echo injected"])
        self.assertEqual(command[-2:], ("-k", "name; echo injected"))

    def test_child_exit_code_is_preserved_and_cwd_is_root(self) -> None:
        with patch.object(self.tasks.subprocess, "run") as run:
            run.return_value.returncode = 7
            self.assertEqual(self.tasks.main(["test", "-x"]), 7)
            self.assertEqual(run.call_args.kwargs["cwd"], ROOT)
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_setup_forces_isolation_only_in_child_environment(self) -> None:
        with patch.dict(self.tasks.os.environ, {"POETRY_VIRTUALENVS_CREATE": "false"}):
            with patch.object(self.tasks.subprocess, "run") as run:
                run.return_value.returncode = 0
                self.assertEqual(self.tasks.main(["setup"]), 0)
                self.assertEqual(
                    run.call_args.kwargs["env"]["POETRY_VIRTUALENVS_CREATE"], "true"
                )
            self.assertEqual(
                self.tasks.os.environ["POETRY_VIRTUALENVS_CREATE"], "false"
            )

    def test_missing_poetry_is_actionable_not_traceback(self) -> None:
        with patch.object(self.tasks.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(self.tasks.main(["lint"]), 127)

    def test_live_does_not_spawn(self) -> None:
        with patch.object(self.tasks.subprocess, "run") as run:
            self.assertEqual(self.tasks.main(["live"]), 2)
            run.assert_not_called()

    def test_doctor_reports_missing_dependencies_without_importing_them(self) -> None:
        with patch.object(
            self.tasks.metadata,
            "version",
            side_effect=self.tasks.metadata.PackageNotFoundError,
        ):
            self.assertEqual(self.tasks.doctor(), 1)

    def test_doctor_passes_with_installed_dependencies(self) -> None:
        with patch.object(self.tasks.metadata, "version", return_value="1.0"):
            with patch.object(self.tasks.sys, "version_info", (3, 12, 0)):
                self.assertEqual(self.tasks.doctor(), 0)

    def test_doctor_rejects_unsupported_python(self) -> None:
        with patch.object(self.tasks.metadata, "version", return_value="1.0"):
            with patch.object(self.tasks.sys, "version_info", (3, 13, 0)):
                self.assertEqual(self.tasks.doctor(), 1)

    def test_makefile_delegates_to_shared_runner(self) -> None:
        content = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("scripts/project.py", content)
        self.assertNotIn("virtualenvs.create false", content)
        self.assertNotIn("checkpoints/final_model.zip", content)

    def test_powershell_uses_argument_arrays_without_expression_evaluation(
        self,
    ) -> None:
        content = (ROOT / "Invoke-Project.ps1").read_text(encoding="utf-8")
        self.assertIn("scripts/project.py", content)
        self.assertNotIn("Invoke-Expression", content)
        self.assertNotIn("virtualenvs.create false", content)

    def test_cmd_keeps_output_visible_and_uses_script_directory(self) -> None:
        content = (ROOT / "invoke-project.cmd").read_text(encoding="utf-8")
        self.assertNotIn("wmic", content.lower())
        self.assertNotIn('> "%LOG_FILE%"', content)
        self.assertIn("%~dp0", content)
        self.assertIn("%*", content)

    def test_help_runs_without_application_dependencies(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "project.py"), "--help"],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("doctor", result.stdout)


if __name__ == "__main__":
    unittest.main()
