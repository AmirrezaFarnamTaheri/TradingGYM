"""
Tests for setup-gui.sh script functionality.

These tests verify the setup script logic without actually running the full script,
which could hang or have side effects in the test environment.
"""

import os
import pytest
import subprocess
import sys
from pathlib import Path


@pytest.fixture
def project_root():
    """Get the project root directory."""
    return Path(__file__).parent.parent.parent


@pytest.fixture
def setup_script(project_root):
    """Get path to the setup-gui.sh script."""
    script_path = project_root / "scripts" / "setup-gui.sh"
    if not script_path.exists():
        pytest.skip(f"Setup script not found at {script_path}")
    return script_path


def test_setup_script_exists(setup_script):
    """Test that the setup script exists and is readable."""
    assert setup_script.exists()
    assert setup_script.is_file()
    assert os.access(setup_script, os.R_OK)


def test_setup_script_is_executable(setup_script):
    """Test that the setup script has executable permissions."""
    # Skip on Windows
    if os.name == "nt":
        pytest.skip("Executable permission check not applicable on Windows")
    assert os.access(setup_script, os.X_OK)


def test_setup_script_has_shebang(setup_script):
    """Test that the script has a proper shebang."""
    with open(setup_script, "r") as f:
        first_line = f.readline()
    assert first_line.startswith("#!"), "Script should have a shebang"
    assert "bash" in first_line.lower(), "Script should use bash"


def test_setup_script_check_flag(setup_script):
    if os.name == "nt":
        pytest.skip("Bash wrapper is not the Windows entrypoint")
    result = subprocess.run(["bash", str(setup_script), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--check" in result.stdout
    assert "--dry-run" in result.stdout


def test_canonical_bootstrap_help(project_root):
    result = subprocess.run([sys.executable, str(project_root / "scripts/gui.py"), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "Poetry" in result.stdout and "npm" in result.stdout


def test_pyproject_toml_exists(project_root):
    """Test that pyproject.toml exists (required by setup script)."""
    pyproject = project_root / "pyproject.toml"
    assert pyproject.exists(), "pyproject.toml required for Python setup"


def test_frontend_directory_exists(project_root):
    """Test that frontend directory exists (required by setup script)."""
    frontend = project_root / "frontend"
    assert frontend.exists(), "frontend directory required"
    assert frontend.is_dir(), "frontend should be a directory"


def test_frontend_package_json_exists(project_root):
    """Test that frontend/package.json exists."""
    package_json = project_root / "frontend" / "package.json"
    assert package_json.exists(), "frontend/package.json required for npm setup"


@pytest.mark.parametrize("env_file", [".env.example"])
def test_env_examples_exist(project_root, env_file):
    """Test that environment file examples exist."""
    env_path = project_root / env_file
    assert env_path.exists(), f"{env_file} should exist as a template"


def test_config_directory_exists(project_root):
    """Test that config directory exists with config files."""
    config_dir = project_root / "config"
    assert config_dir.exists(), "config directory should exist"
    assert config_dir.is_dir(), "config should be a directory"

    # Check for at least one config file
    config_files = list(config_dir.glob("*.yaml"))
    assert len(config_files) > 0, "At least one YAML config file should exist"


def test_apps_api_main_exists(project_root):
    """Test that the API main file exists (required for starting API server)."""
    api_main = project_root / "apps" / "api" / "main.py"
    assert api_main.exists(), "apps/api/main.py required for API server"


def test_script_rejects_bad_ports_before_launch(project_root):
    result = subprocess.run([sys.executable, str(project_root / "scripts/gui.py"), "start", "--api-port", "70000"], capture_output=True, text=True)
    assert result.returncode == 2
    assert "between 1 and 65535" in result.stderr
