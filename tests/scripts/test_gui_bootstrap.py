import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("gui_bootstrap", Path(__file__).parents[2] / "scripts/gui.py")
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)


class BootstrapTests(unittest.TestCase):
    def test_check_cannot_install_write_or_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "frontend").mkdir()
            for name in ("pyproject.toml", "poetry.lock", "frontend/package-lock.json"):
                (root / name).write_text("fixture")
            before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            with patch.object(gui, "ROOT", root), patch.object(gui.shutil, "which", return_value="fixture-tool"), patch.object(gui.subprocess, "run", side_effect=AssertionError("unexpected install")), patch.object(gui.subprocess, "Popen", side_effect=AssertionError("unexpected launch")):
                self.assertEqual(gui.main(["setup", "--check"]), 0)
                self.assertEqual(gui.main(["start", "--dry-run"]), 0)
            self.assertEqual(before, sorted(str(p.relative_to(root)) for p in root.rglob("*")))

    def test_dotenv_is_literal_and_explicit_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("API_KEY='fixture-key'\nVALUE=$(do-not-execute)\n")
            with patch.object(gui, "ROOT", root), patch.dict(os.environ, {"API_KEY": "caller-key"}, clear=True):
                env = gui.environment()
            self.assertEqual(env["API_KEY"], "caller-key")
            self.assertEqual(env["VALUE"], "$(do-not-execute)")
            self.assertEqual(env["POETRY_VIRTUALENVS_CREATE"], "true")

    def test_readiness_uses_contract_and_checks_process_exit(self):
        class Exited:
            def poll(self): return 1
        self.assertEqual(gui.HEALTH_PATH, "/api/v1/ready")
        with self.assertRaisesRegex(RuntimeError, "exited before readiness"):
            gui.wait_ready(Exited(), "http://127.0.0.1:1/api/v1/ready")

    def test_insecure_api_default_is_rejected_before_spawn(self):
        with patch.object(gui, "environment", return_value={"API_KEY": "changeme-insecure-default-dev-only"}), patch.object(gui.subprocess, "Popen", side_effect=AssertionError("unexpected launch")):
            with self.assertRaisesRegex(RuntimeError, "Configure API_KEY"):
                gui.start(8000, 3000, True)

    def test_configuration_is_private_and_never_overwrites_existing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(gui, "ROOT", root):
                gui.configure()
                original = (root / ".env").read_bytes()
                gui.configure()
            self.assertEqual(original, (root / ".env").read_bytes())
            if os.name == "posix": self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__": unittest.main()
