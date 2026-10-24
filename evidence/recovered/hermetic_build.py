#!/usr/bin/env python3
"""Plan and execute Axiom Forge's locked build in an isolated environment."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.verify_dependency_locks import analyze_dependency_locks
except ModuleNotFoundError:  # Direct script execution sets sys.path to scripts/.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.verify_dependency_locks import analyze_dependency_locks


@dataclass(frozen=True)
class ToolchainPolicy:
    python_min: tuple[int, int] = (3, 10)
    python_max: tuple[int, int] = (3, 12)
    node_majors: tuple[int, ...] = (20, 22)
    npm_min_major: int = 10
    poetry_version: str = "1.8.5"


POLICY = ToolchainPolicy()

_DEFAULT_STAGE_TIMEOUT_SECONDS = 1800.0
_DEFAULT_MAX_OUTPUT_CHARS = 65536
_SENSITIVE_NAME_FRAGMENTS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "AUTHORIZATION",
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)([^\s]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIAL_RE = re.compile(r"(https?://)([^/@\s:]+):([^/@\s]+)@")


def _major_minor(value: str) -> tuple[int, int] | None:
    match = re.search(r"(\d+)\.(\d+)", value)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _major(value: str) -> int | None:
    match = re.search(r"(\d+)", value)
    return int(match.group(1)) if match else None


def evaluate_toolchain_versions(
    *,
    python_version: str,
    node_version: str,
    npm_version: str,
    poetry_version: str,
    policy: ToolchainPolicy = POLICY,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    python_pair = _major_minor(python_version)
    if python_pair is None or not (
        policy.python_min <= python_pair <= policy.python_max
    ):
        issues.append(
            {
                "code": "unsupported_python",
                "message": (
                    f"Python {policy.python_min[0]}.{policy.python_min[1]}-"
                    f"{policy.python_max[0]}.{policy.python_max[1]} is required; found {python_version!r}"
                ),
            }
        )
    node_major = _major(node_version)
    if node_major not in policy.node_majors:
        issues.append(
            {
                "code": "unsupported_node",
                "message": f"Node major must be one of {list(policy.node_majors)}; found {node_version!r}",
            }
        )
    npm_major = _major(npm_version)
    if npm_major is None or npm_major < policy.npm_min_major:
        issues.append(
            {
                "code": "unsupported_npm",
                "message": f"npm >= {policy.npm_min_major} is required; found {npm_version!r}",
            }
        )
    if policy.poetry_version not in poetry_version:
        issues.append(
            {
                "code": "unsupported_poetry",
                "message": f"Poetry {policy.poetry_version} is required; found {poetry_version!r}",
            }
        )
    return issues


def build_plan(
    root: Path, *, scope: str = "all", run_tests: bool = True
) -> list[dict[str, Any]]:
    root = root.resolve()
    stages: list[dict[str, Any]] = [
        {
            "name": "verify_dependency_locks",
            "cwd": str(root),
            "command": [
                sys.executable,
                "scripts/verify_dependency_locks.py",
                "--root",
                ".",
            ],
        }
    ]
    if scope in ("python", "all"):
        stages.extend(
            [
                {
                    "name": "poetry_lock_consistency",
                    "cwd": str(root),
                    "command": ["poetry", "check", "--lock"],
                },
                {
                    "name": "python_locked_install",
                    "cwd": str(root),
                    "command": [
                        "poetry",
                        "install",
                        "--sync",
                        "--with",
                        "dev",
                        "--no-interaction",
                    ],
                },
                {
                    "name": "python_compile",
                    "cwd": str(root),
                    "command": [
                        "poetry",
                        "run",
                        "python",
                        "-m",
                        "compileall",
                        "-q",
                        "apps",
                        "trading_gym",
                        "axiomforge",
                        "scripts",
                        "tests",
                    ],
                },
            ]
        )
        if run_tests:
            stages.append(
                {
                    "name": "python_tests",
                    "cwd": str(root),
                    "command": ["poetry", "run", "pytest", "-q"],
                }
            )
    if scope in ("frontend", "all"):
        frontend = root / "frontend"
        stages.extend(
            [
                {
                    "name": "frontend_locked_install",
                    "cwd": str(frontend),
                    "command": ["npm", "ci", "--no-audit", "--no-fund"],
                },
                {
                    "name": "frontend_wallet_contract",
                    "cwd": str(frontend),
                    "command": ["npm", "run", "test:wallet-contract"],
                },
            ]
        )
        if run_tests:
            stages.append(
                {
                    "name": "frontend_tests",
                    "cwd": str(frontend),
                    "command": ["npm", "run", "test:ci"],
                }
            )
        stages.append(
            {
                "name": "frontend_build",
                "cwd": str(frontend),
                "command": ["npm", "run", "build"],
            }
        )
    return stages


def _tool_version(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"
    return (
        (result.stdout or "").strip().splitlines()[0]
        if result.stdout
        else f"exit={result.returncode}"
    )


def inspect_toolchain() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "node": _tool_version(["node", "--version"]),
        "npm": _tool_version(["npm", "--version"]),
        "poetry": _tool_version(["poetry", "--version"]),
    }


def _base_environment(*, cache_root: Path, offline: bool) -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "PIP_EXTRA_INDEX_URL",
        "PIP_TRUSTED_HOST",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "NPM_CONFIG_USERCONFIG",
        "npm_config_userconfig",
    ):
        env.pop(name, None)
    env.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PIP_INDEX_URL": "https://pypi.org/simple",
            "POETRY_NO_INTERACTION": "1",
            "POETRY_CACHE_DIR": str(cache_root / "poetry"),
            "POETRY_CONFIG_DIR": str(cache_root / "poetry-config"),
            "POETRY_VIRTUALENVS_PATH": str(cache_root / "poetry-virtualenvs"),
            "POETRY_VIRTUALENVS_CREATE": "true",
            "npm_config_cache": str(cache_root / "npm"),
            "npm_config_registry": "https://registry.npmjs.org/",
            "npm_config_replace_registry_host": "never",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "npm_config_update_notifier": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
        }
    )
    if offline:
        env.update({"PIP_NO_INDEX": "1", "npm_config_offline": "true"})
    return env


def _sensitive_values_from_environment(environment: dict[str, str]) -> tuple[str, ...]:
    values: set[str] = set()
    for name, value in environment.items():
        upper = name.upper()
        if (
            value
            and len(value) >= 6
            and any(fragment in upper for fragment in _SENSITIVE_NAME_FRAGMENTS)
        ):
            values.add(value)
    return tuple(sorted(values, key=len, reverse=True))


def _redact_output(value: str, sensitive_values: Sequence[str]) -> str:
    redacted = value
    for secret in sorted(
        {item for item in sensitive_values if len(item) >= 6}, key=len, reverse=True
    ):
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _BEARER_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", redacted)
    redacted = _URL_CREDENTIAL_RE.sub(r"\1[REDACTED]@", redacted)
    return redacted


def _bound_output(value: str, max_chars: int) -> tuple[str, bool]:
    if max_chars < 128:
        raise ValueError("max_output_chars must be at least 128")
    if len(value) <= max_chars:
        return value, False
    marker = "\n... [OUTPUT TRUNCATED] ...\n"
    available = max_chars - len(marker)
    head = available // 2
    tail = available - head
    return value[:head] + marker + value[-tail:], True


class _BoundedStreamCapture:
    """Drain a subprocess stream while retaining only a bounded head and tail."""

    def __init__(self, max_chars: int) -> None:
        if max_chars < 128:
            raise ValueError("max_output_chars must be at least 128")
        self.max_chars = max_chars
        self.max_bytes = max_chars * 4
        self.head_budget = self.max_bytes // 2
        self.tail_budget = self.max_bytes - self.head_budget
        self.head = bytearray()
        self.tail = bytearray()
        self.bytes_seen = 0

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.bytes_seen += len(chunk)
        offset = 0
        if len(self.head) < self.head_budget:
            take = min(self.head_budget - len(self.head), len(chunk))
            self.head.extend(chunk[:take])
            offset = take
        if offset < len(chunk):
            self.tail.extend(chunk[offset:])
            if len(self.tail) > self.tail_budget:
                del self.tail[: len(self.tail) - self.tail_budget]

    @property
    def bytes_retained(self) -> int:
        return len(self.head) + len(self.tail)

    def render(self) -> tuple[str, bool]:
        truncated = self.bytes_seen > self.bytes_retained
        if truncated:
            data = (
                bytes(self.head) + b"\n... [OUTPUT TRUNCATED] ...\n" + bytes(self.tail)
            )
        else:
            data = bytes(self.head) + bytes(self.tail)
        value = data.decode("utf-8", errors="replace")
        bounded, char_truncated = _bound_output(value, self.max_chars)
        return bounded, truncated or char_truncated


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_stage_command(
    command: Sequence[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout_seconds: float,
    max_output_chars: int,
) -> tuple[int, bool, _BoundedStreamCapture]:
    capture = _BoundedStreamCapture(max_output_chars)
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "bufsize": 0,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except OSError as exc:
        capture.feed(f"Unable to start command: {exc}\n".encode())
        return 127, False, capture

    assert process.stdout is not None

    def drain() -> None:
        while True:
            chunk = process.stdout.read(64 * 1024)
            if not chunk:
                break
            capture.feed(chunk)

    reader = threading.Thread(target=drain, name=f"capture-{process.pid}", daemon=True)
    reader.start()
    timed_out = False
    try:
        exit_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_tree(process)
        exit_code = 124
        capture.feed(
            f"\nStage exceeded timeout of {timeout_seconds:g} seconds.\n".encode()
        )
    finally:
        reader.join(timeout=10)
        if reader.is_alive():
            process.stdout.close()
            reader.join(timeout=1)
    return exit_code, timed_out, capture


def execute_plan(
    plan: Sequence[dict[str, Any]],
    *,
    evidence_path: Path,
    cache_root: Path,
    offline: bool,
    stage_timeout_seconds: float = _DEFAULT_STAGE_TIMEOUT_SECONDS,
    max_output_chars: int = _DEFAULT_MAX_OUTPUT_CHARS,
    sensitive_values: Sequence[str] | None = None,
) -> dict[str, Any]:
    if stage_timeout_seconds <= 0:
        raise ValueError("stage_timeout_seconds must be positive")
    cache_root.mkdir(parents=True, exist_ok=True)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    env = _base_environment(cache_root=cache_root, offline=offline)
    redaction_values = tuple(
        sensitive_values or ()
    ) + _sensitive_values_from_environment(env)
    stages: list[dict[str, Any]] = []
    overall_status = "verified"
    started = time.monotonic()
    for stage in plan:
        stage_started = time.monotonic()
        command = [str(item) for item in stage["command"]]
        exit_code, timed_out, capture = _run_stage_command(
            command,
            cwd=stage["cwd"],
            env=env,
            timeout_seconds=stage_timeout_seconds,
            max_output_chars=max_output_chars,
        )
        raw_output, output_truncated = capture.render()
        safe_output = _redact_output(raw_output, redaction_values)
        safe_command = [_redact_output(item, redaction_values) for item in command]
        record = {
            "name": stage["name"],
            "cwd": stage["cwd"],
            "command": safe_command,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - stage_started, 3),
            "output": safe_output,
            "output_truncated": output_truncated,
            "output_bytes_seen": capture.bytes_seen,
            "output_bytes_retained": capture.bytes_retained,
        }
        stages.append(record)
        if exit_code != 0:
            overall_status = "rejected"
            break
    report = {
        "schema_version": 1,
        "status": overall_status,
        "offline": offline,
        "duration_seconds": round(time.monotonic() - started, 3),
        "toolchain": inspect_toolchain(),
        "policy": asdict(POLICY),
        "cache_root": str(cache_root),
        "capture_mode": "bounded_stream",
        "stage_timeout_seconds": stage_timeout_seconds,
        "max_output_chars": max_output_chars,
        "stages": stages,
    }
    evidence_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _print(payload: Any) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "check", "run"))
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--scope", choices=("locks", "python", "frontend", "all"), default="all"
    )
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument(
        "--stage-timeout-seconds", type=float, default=_DEFAULT_STAGE_TIMEOUT_SECONDS
    )
    parser.add_argument(
        "--max-output-chars", type=int, default=_DEFAULT_MAX_OUTPUT_CHARS
    )
    args = parser.parse_args()

    root = args.root.resolve()
    lock_report = analyze_dependency_locks(root)
    plan = build_plan(root, scope=args.scope, run_tests=not args.skip_tests)
    if args.scope == "locks":
        plan = plan[:1]

    if args.command == "plan":
        _print(
            {
                "schema_version": 1,
                "status": lock_report["status"],
                "policy": asdict(POLICY),
                "lock_report": lock_report,
                "stages": plan,
            }
        )
        return 0 if lock_report["status"] == "verified" else 1

    tools = inspect_toolchain()
    version_issues = evaluate_toolchain_versions(
        python_version=tools["python"],
        node_version=tools["node"],
        npm_version=tools["npm"],
        poetry_version=tools["poetry"],
    )
    check_report = {
        "schema_version": 1,
        "status": (
            "verified"
            if not version_issues and lock_report["status"] == "verified"
            else "rejected"
        ),
        "toolchain": tools,
        "policy": asdict(POLICY),
        "lock_report": lock_report,
        "issues": version_issues,
    }
    if args.command == "check":
        _print(check_report)
        return 0 if check_report["status"] == "verified" else 1
    if check_report["status"] != "verified":
        _print(check_report)
        return 1

    evidence = args.evidence or root / "build" / "hermetic" / "report.json"
    temporary_cache: tempfile.TemporaryDirectory[str] | None = None
    if args.cache_root is None:
        temporary_cache = tempfile.TemporaryDirectory(
            prefix="axiomforge-hermetic-cache-"
        )
        cache_root = Path(temporary_cache.name)
    else:
        cache_root = args.cache_root.resolve()
    try:
        report = execute_plan(
            plan,
            evidence_path=evidence,
            cache_root=cache_root,
            offline=args.offline,
            stage_timeout_seconds=args.stage_timeout_seconds,
            max_output_chars=args.max_output_chars,
        )
    finally:
        if temporary_cache is not None:
            temporary_cache.cleanup()
    _print(report)
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
