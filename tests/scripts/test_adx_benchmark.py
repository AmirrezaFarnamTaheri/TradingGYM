"""Offline native ADX benchmark CLI contracts; never contacts a venue."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "benchmark_adx.py"


def run(*arguments, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=ROOT.parent,
        capture_output=True,
        text=True,
        env=env,
    )


def test_benchmark_help_and_invalid_size():
    help_result = run("--help")
    assert help_result.returncode == 0
    assert "--bars" in help_result.stdout
    invalid = run("--bars", "0")
    assert invalid.returncode == 2


def test_benchmark_refuses_to_claim_native_performance_when_disabled():
    result = run(
        "--bars", "30", "--repeat", "1", env={**os.environ, "AXIOM_KERNEL_DISABLE": "1"}
    )
    assert result.returncode == 2
    assert "ADX kernel unavailable" in result.stderr


def test_benchmark_verifies_parity_and_reports_both_execution_times():
    from trading_gym.playground import kernel

    library = kernel.load_kernel()
    if library is None or not hasattr(library, "axiom_wilder_adx"):
        pytest.skip("native ADX artifact unavailable")
    result = run("--bars", "300", "--repeat", "1")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["results"][0]["bit_identical"] is True
    assert payload["results"][0]["python_median_seconds"] > 0
    assert payload["results"][0]["rust_median_seconds"] > 0
    assert payload["results"][0]["speedup"] > 0
