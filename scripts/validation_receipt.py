"""Record an executed command or classify exported GitHub job metadata.

No network requests, workflow reruns or external state changes are performed.
This receipt describes its command scope; it never certifies unexecuted paths.
"""

from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def source_identity(declared_sha: str) -> dict:
    """Admit only a clean Git checkout at exactly the declared revision."""
    if not re.fullmatch(r"[0-9a-f]{40}", declared_sha):
        raise ValueError("source SHA must be a full lowercase Git commit hash")

    def git(*args: str) -> bytes:
        try:
            result = subprocess.run(
                ["git", *args], cwd=ROOT, capture_output=True, check=False
            )
        except OSError as exc:
            raise ValueError("git is required for a revision-bound receipt") from exc
        if result.returncode != 0:
            raise ValueError("git source identity is unavailable")
        return result.stdout

    checkout = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
    if checkout != ROOT.resolve():
        raise ValueError("receipt source must be the repository root")
    observed = git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if observed != declared_sha:
        raise ValueError("declared commit does not match checked-out HEAD")
    status = git(
        "status", "--porcelain", "--untracked-files=normal"
    ).decode().splitlines()
    if status:
        raise ValueError("dirty checkout cannot produce a revision-bound receipt")
    inventory = git("ls-files", "--stage")
    if not inventory:
        raise ValueError("source Git index is empty")
    worktree_diff = git("diff", "--binary", "HEAD", "--")
    if worktree_diff:
        raise ValueError("dirty checkout cannot produce a revision-bound receipt")
    return {
        "declared_base_sha": declared_sha,
        "observed_head_sha": observed,
        "index_inventory_sha256": hashlib.sha256(inventory).hexdigest(),
        "worktree_diff_sha256": hashlib.sha256(worktree_diff).hexdigest(),
        "working_tree_status": status,
    }


def verify_unchanged(declared_sha: str, initial: dict) -> None:
    """The source and index must remain stable through the named command."""
    if source_identity(declared_sha) != initial:
        raise ValueError("source changed during validation")


# Require every CI job, including each matrix cell; keep in sync with
# .github/workflows/ci.yml. The inventory test detects workflow drift.
REQUIRED_CI_JOBS = frozenset(
    {
        "Lint & Format",
        "Type Checking",
        "Tests (ubuntu-latest, 3.11)",
        "Tests (ubuntu-latest, 3.12)",
        "Tests (windows-latest, 3.11)",
        "Tests (windows-latest, 3.12)",
        "Research and Paper Contracts",
        "Integration Tests",
        "Frontend Tests",
        "Security Scan",
        "Rust Kernel & Parity (ADR-035)",
        "All Checks Passed",
    }
)

# Only this completed, scheduled informational skip is non-gating.
OPTIONAL_SKIPPED_JOBS = frozenset({"Dependency Scan (weekly, informational)"})

// Each required CI job must actually execute its verification steps. A green
// checkout-only job is not sufficient evidence. Keep in sync with ci.yml.
REQUIRED_CI_PROOF_STEPS = {
    "Lint & Format": ("Run Ruff linter", "Run Black formatter check", "Run Flake8"),
    "Type Checking": ("Run MyPy",),
    "Tests (ubuntu-latest, 3.11)": ("Run unit tests",),
    "Tests (ubuntu-latest, 3.12)": ("Run unit tests with coverage",),
    "Tests (windows-latest, 3.11)": ("Run unit tests",),
    "Tests (windows-latest, 3.12)": ("Run unit tests",),
    "Research and Paper Contracts": (
        "Check research, account, provider and API contracts with real dependencies",
    ),
    "Integration Tests": ("Run integration tests",),
    "Frontend Tests": ("Run tests", "Run native Node contract tests", "Build frontend"),
    "Security Scan": ("Run Bandit security linter",),
    "Rust Kernel & Parity (ADR-035)": (
        "Cargo fmt",
        "Clippy",
        "Crate unit tests",
        "Build release cdylib",
        "Parity suite against the compiled kernel",
        "Playground and forge suites against the compiled kernel",
    ),
    "All Checks Passed": ("Require every verification job to succeed",),
}



def classify_jobs(
    document: dict, expected_sha: str, expected_run_id: int | None = None
) -> dict:
    if expected_run_id is not None and (
        type(expected_run_id) is not int or expected_run_id <= 0
    ):
        raise ValueError("expected GitHub run ID must be a positive integer")
    jobs = document.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("jobs export must contain a non-empty jobs list")
    results = []
    for job in jobs:
        if not isinstance(job, dict) or job.get("head_sha") != expected_sha:
            raise ValueError("job SHA missing or does not match the assessed revision")
        steps = job.get("steps")
        runner = bool(job.get("runner_name") or job.get("runner_id"))
        if job.get("conclusion") == "skipped":
            # The scheduled informational scan deliberately skips pull requests.
            # Only a completed skip of this exact job is non-gating.
            evidence = (
                "intentional_non_gating_skip"
                if job.get("name") in OPTIONAL_SKIPPED_JOBS
                and job.get("status") == "completed"
                else "skipped"
            )
        elif isinstance(steps, list) and steps and runner:
            evidence = "execution_observed"
        elif (
            (steps is None or steps == [])
            and not runner
            and job.get("status") == "completed"
        ):
            evidence = "no_execution_evidence"
        else:
            evidence = "incomplete_metadata_or_pending"
        expected_steps = REQUIRED_CI_PROOF_STEPS.get(job.get("name"), ())
        present_steps = steps if isinstance(steps, list) else []
        missing_gate_steps = []
        for step_name in expected_steps:
            matches = [
                step
                for step in present_steps
                if isinstance(step, dict) and step.get("name") == step_name
            ]
            if (
                len(matches) != 1
                or matches[0].get("status") != "completed"
                or matches[0].get("conclusion") != "success"
            ):
                missing_gate_steps.append(step_name)
        results.append(
            {
                "missing_or_unsuccessful_gate_steps": missing_gate_steps,
                "id": job.get("id"),
                "name": job.get("name"),
                "url": job.get("html_url"),
                "conclusion": job.get("conclusion"),
                "status": job.get("status"),
                "run_id": job.get("run_id"),
                "run_attempt": job.get("run_attempt"),
                "evidence": evidence,
                "step_count": len(steps) if isinstance(steps, list) else None,
            }
        )
    counts = Counter(str(item["name"]) for item in results)
    observed_names = set(counts)
    missing_required_jobs = sorted(REQUIRED_CI_JOBS - observed_names)
    duplicate_job_names = sorted(name for name, count in counts.items() if count > 1)
    unexpected_job_names = sorted(
        observed_names - REQUIRED_CI_JOBS - OPTIONAL_SKIPPED_JOBS
    )
    complete_job_inventory = not (
        missing_required_jobs or duplicate_job_names or unexpected_job_names
    )
    complete_verification_step_inventory = (
        set(REQUIRED_CI_PROOF_STEPS) == REQUIRED_CI_JOBS
    )
    # Successful jobs from two distinct runs on the same commit must not be
    # stitched together into one fabricated green receipt. A GitHub rerun may
    # legitimately contain jobs from different attempts of the SAME run.
    valid_run_ids = {
        item["run_id"]
        for item in results
        if type(item["run_id"]) is int and item["run_id"] > 0
    }
    unbound_job_names = sorted(
        str(item["name"])
        for item in results
        if type(item["run_id"]) is not int or item["run_id"] <= 0
    )
    single_run_identity = len(valid_run_ids) == 1 and not unbound_job_names
    # A coherent export from another workflow run must never certify this run.
    bound_to_expected_run_id = (
        single_run_identity
        and expected_run_id is not None
        and expected_run_id in valid_run_ids
    )
    return {
        "jobs": results,
        "missing_required_jobs": missing_required_jobs,
        "duplicate_job_names": duplicate_job_names,
        "unexpected_job_names": unexpected_job_names,
        "complete_job_inventory": complete_job_inventory,
        "complete_verification_step_inventory": complete_verification_step_inventory,
        "single_run_identity": single_run_identity,
        "expected_run_id": expected_run_id,
        "bound_to_expected_run_id": bound_to_expected_run_id,
        "unbound_job_names": unbound_job_names,
        "observed_run_ids": sorted(valid_run_ids),
        "all_executed_successfully": (
            complete_job_inventory
            and complete_verification_step_inventory
            and single_run_identity
            and bound_to_expected_run_id
            and all(
                (
                    item["evidence"] == "execution_observed"
                    and not item["missing_or_unsuccessful_gate_steps"]
                    and item["conclusion"] == "success"
                    and item["status"] == "completed"
                )
                or (
                    item["evidence"] == "intentional_non_gating_skip"
                    and item["conclusion"] == "skipped"
                )
                for item in results
            )
        ),
        "admission_cause": "unverified",
        "next_external_step": "Inspect the GitHub job/account admission annotation. Resolve its actual cause, then obtain a started current-revision job and retain logs; this tool cannot enable runner admission.",
    }


def ensure_external_output(path: Path) -> None:
    """A receipt cannot modify the very checkout whose identity it certifies."""
    root = ROOT.resolve()
    target = path.expanduser()
    # Cover both lexical paths and paths entering through directory symlinks.
    if (
        target.absolute().is_relative_to(root)
        or target.resolve().is_relative_to(root)
    ):
        raise ValueError("receipt output must be outside the source checkout")


def write_receipt(path: Path, document: dict) -> None:
    ensure_external_output(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def execute(command: list[str], timeout: int) -> dict:
    if not command:
        raise ValueError("provide the exact command after --")
    started = time.monotonic()
    # Capture to a disposable file, not memory; no potentially secret log text
    # is copied into the receipt. The digest binds the observed output stream.
    with tempfile.TemporaryFile() as log:
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            code, timed_out = result.returncode, False
        except subprocess.TimeoutExpired:
            code, timed_out = 124, True
        log.seek(0)
        output = hashlib.sha256()
        count = 0
        for chunk in iter(lambda: log.read(1024 * 1024), b""):
            count += len(chunk)
            output.update(chunk)
    return {
        "command": command,
        "returncode": code,
        "timeout": timed_out,
        "duration_seconds": round(time.monotonic() - started, 6),
        "output_bytes": count,
        "output_sha256": output.hexdigest(),
        "outcome": "passed" if code == 0 else "failed",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--jobs-json", type=Path)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 3600:
        parser.error("timeout must be 1..3600 seconds")
    if args.jobs_json:
        if args.run_id is None or args.run_id <= 0:
            parser.error("--jobs-json requires a positive --run-id")
    elif args.run_id is not None:
        parser.error("--run-id requires --jobs-json")
    try:
        ensure_external_output(args.output)
    except ValueError as exc:
        parser.error(str(exc))
    receipt = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": args.scope,
        "qualification": "named-command-or-metadata-only",
        "source": source_identity(args.source_sha),
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
        },
    }
    if args.jobs_json:
        if args.command:
            parser.error("job metadata and command execution are separate modes")
        if args.jobs_json.stat().st_size > 8 * 1024 * 1024:
            parser.error("jobs export exceeds 8 MiB")
        receipt["ci"] = classify_jobs(
            json.loads(args.jobs_json.read_text()),
            args.source_sha,
            expected_run_id=args.run_id,
        )
        code = 0 if receipt["ci"]["all_executed_successfully"] else 1
    else:
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        receipt["execution"] = execute(command, args.timeout)
        code = receipt["execution"]["returncode"]
        try:
            verify_unchanged(args.source_sha, receipt["source"])
        except ValueError as exc:
            receipt["execution"]["outcome"] = "unqualified"
            receipt["execution"]["qualification_error"] = str(exc)
            code = 1
    write_receipt(args.output, receipt)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
