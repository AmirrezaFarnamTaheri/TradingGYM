"""Run research/paper boundary tests with installed dependencies and isolated state.

This focused gate intentionally excludes tests/conftest.py, whose optional
dependency substitutes remain useful for lightweight legacy unit tests. It
supplements the full suite; neither invocation replaces the other.
"""

from __future__ import annotations

import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    "tests/meta_patch/test_*.py",
    "tests/research_contracts/test_*.py",
    "tests/intelligence/test_*_patch.py",
)
BOUNDARY_TESTS = (
    "tests/adapters/test_observation_recorder.py",
    "tests/agent/test_memory_forge.py",
    "tests/api/test_built_ui.py",
    "tests/api/test_example_run_http.py",
    "tests/api/test_example_runs.py",
    "tests/api/test_training_service.py",
    "tests/contracts/test_openapi_contract.py",
    "tests/contracts/test_openapi_publishing.py",
    "tests/execution/test_avg_entry_price.py",
    "tests/execution/test_decimal_precision.py",
    "tests/execution/test_engine_depth_walk.py",
    "tests/execution/test_execution_engine.py",
    "tests/execution/test_margin_multiplier.py",
    "tests/intelligence/test_dynamic_resilience.py",
    "tests/operations/test_recovery_scope.py",
    "tests/operations/test_storage_health.py",
    "tests/scripts/test_gui_bootstrap.py",
    "tests/scripts/test_setup_gui_sh.py",
    "tests/scripts/test_validation_receipt.py",
)
DEPENDENCIES = {
    "numpy": "numpy",
    "pandas": "pandas",
    "scipy": "scipy",
    "torch": "torch",
    "stable_baselines3": "stable-baselines3",
    "gymnasium": "gymnasium",
    "numba": "numba",
    "optuna": "optuna",
    "pydantic": "pydantic",
    "fastapi": "fastapi",
    "httpx": "httpx",
    "onnx": "onnx",
    "onnxruntime": "onnxruntime",
    "pytest": "pytest",
}


def test_paths() -> list[str]:
    paths = set(BOUNDARY_TESTS)
    for pattern in PATTERNS:
        matches = [path.relative_to(ROOT).as_posix() for path in ROOT.glob(pattern)]
        if not matches:
            raise ValueError(f"Contract test group is missing: {pattern}")
        paths.update(matches)
    missing = [path for path in sorted(paths) if not (ROOT / path).is_file()]
    if missing:
        raise ValueError(f"Contract test files are missing: {', '.join(missing)}")
    return sorted(paths)


def installed_versions() -> dict[str, str]:
    versions = {}
    for module_name, distribution in DEPENDENCIES.items():
        module = importlib.import_module(module_name)
        if not getattr(module, "__file__", None):
            raise ImportError(f"{module_name} is a substitute, not an installed module")
        versions[distribution] = metadata.version(distribution)
    return versions


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    try:
        paths = test_paths()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if arguments == ["--list"]:
        print("\n".join(paths))
        return 0
    with tempfile.TemporaryDirectory(prefix="tradinggym-contracts-") as state:
        overrides = {
            "TRADING_GYM_HOME": str(Path(state) / "application"),
            "MPLCONFIGDIR": str(Path(state) / "matplotlib"),
            "TRADING_GYM_REQUIRE_REAL_DEPS": "1",
        }
        previous = {key: os.environ.get(key) for key in overrides}
        os.environ.update(overrides)
        try:
            # SB3 imports Matplotlib, so establish the writable isolated cache
            # before dependency admission as well as before the pytest child.
            try:
                versions = installed_versions()
            except (ImportError, OSError, metadata.PackageNotFoundError) as exc:
                print(
                    f"Contract dependency admission failed: {exc}\n"
                    "Install the supported runtime with: poetry install --with dev,experiments",
                    file=sys.stderr,
                )
                return 2
            print(
                json.dumps(
                    {
                        "python": sys.version,
                        "packages": versions,
                        "test_files": len(paths),
                    }
                ),
                flush=True,
            )
            command = [
                sys.executable,
                "-m",
                "pytest",
                "--noconftest",
                "-q",
                *paths,
                *arguments,
            ]
            return subprocess.run(command, cwd=ROOT, env=dict(os.environ)).returncode
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
