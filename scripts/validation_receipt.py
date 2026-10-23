"""Record an executed command or classify exported GitHub job metadata.

No network requests, workflow reruns or external state changes are performed.
This receipt describes its command scope; it never certifies unexecuted paths.
"""

from __future__ import annotations
import argparse
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


def classify_jobs(document: dict, expected_sha: str) -> dict:
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
            evidence = "skipped"
        elif isinstance(steps, list) and steps and runner:
            evidence = "execution_observed"
        elif (steps is None or steps == []) and not runner and job.get("status") == "completed":
            evidence = "no_execution_evidence"
        else:
            evidence = "incomplete_metadata_or_pending"
        results.append(
            {
                "id": job.get("id"),
                "name": job.get("name"),
                "url": job.get("html_url"),
                "conclusion": job.get("conclusion"),
                "evidence": evidence,
                "step_count": len(steps) if isinstance(steps, list) else None,
            }
        )
    return {
        "jobs": results,
        "all_executed_successfully": all(
            item["evidence"] == "execution_observed" and item["conclusion"] == "success"
            for item in results
        ),
        "admission_cause": "unverified",
        "next_external_step": "Inspect the GitHub job/account admission annotation. Resolve its actual cause, then obtain a started current-revision job and retain logs; this tool cannot enable runner admission.",
    }


def write_receipt(path: Path, document: dict) -> None:
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
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 3600:
        parser.error("timeout must be 1..3600 seconds")
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
            json.loads(args.jobs_json.read_text()), args.source_sha
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
