"""Bounded metadata-only headroom checks; never a store integrity verdict."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import stat
import time

from trading_gym.common.persistence import MAX_DOCUMENT_BYTES
from trading_gym.governance.ledger import MAX_LEDGER_BYTES


@dataclass(frozen=True)
class StoreSpec:
    name: str
    relative_path: str
    limit_bytes: int | None


DEFAULT_STORES = (
    StoreSpec("proposals", "proposals.json", MAX_DOCUMENT_BYTES),
    StoreSpec("experiments", "experiments.json", MAX_DOCUMENT_BYTES),
    StoreSpec("providers", "providers.json", MAX_DOCUMENT_BYTES),
    StoreSpec("audit-ledger", "audit-ledger.jsonl", MAX_LEDGER_BYTES),
    # No global byte cap exists for the attribution database/WAL. Do not invent one.
    StoreSpec("decision-journal", "intelligence/decisions.sqlite3", None),
    StoreSpec("decision-journal-wal", "intelligence/decisions.sqlite3-wal", None),
    StoreSpec("decision-journal-shm", "intelligence/decisions.sqlite3-shm", None),
)


def inspect_store(
    root: Path, spec: StoreSpec, *, warning_fraction: float = 0.8
) -> dict:
    if not 0 < warning_fraction < 1 or (
        spec.limit_bytes is not None and spec.limit_bytes <= 0
    ):
        raise ValueError("invalid store headroom policy")
    relative = Path(spec.relative_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("store path must be confined")
    start = time.perf_counter_ns()
    report = {
        "name": spec.name,
        "limit_bytes": spec.limit_bytes,
        "size_bytes": None,
        "headroom_bytes": None,
        "warning_fraction": warning_fraction,
        "integrity_checked": False,
    }
    try:
        current = root
        for part in ("", *relative.parts):
            current = current / part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise ValueError("symbolic link")
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("not a regular file")
        size = metadata.st_size
        if spec.limit_bytes is None:
            report.update(size_bytes=size, status="limit_unconfigured")
        else:
            report.update(
                size_bytes=size,
                headroom_bytes=max(0, spec.limit_bytes - size),
                status=(
                    "limit_reached"
                    if size >= spec.limit_bytes
                    else (
                        "near_limit"
                        if size >= spec.limit_bytes * warning_fraction
                        else "within_limit"
                    )
                ),
            )
    except FileNotFoundError:
        report.update(status="absent", size_bytes=0, headroom_bytes=spec.limit_bytes)
    except (ValueError, OSError):
        report.update(status="unavailable")
    report["metadata_probe_ms"] = (time.perf_counter_ns() - start) / 1_000_000
    return report


def _named_inventory(
    root: Path, directory: str, files: tuple[tuple[str, int], ...], maximum: int
) -> tuple[list[dict], str]:
    parent = root / directory
    if root.is_symlink() or parent.is_symlink():
        return [], "unavailable"
    if not parent.exists():
        return [], "absent"
    output = []
    status = "complete"
    try:
        for index, child in enumerate(parent.iterdir()):
            if index >= maximum:
                return output, "bounded_inventory_exhausted"
            if child.is_symlink():
                status = "unavailable"
                continue
            if child.is_dir():
                for filename, limit in files:
                    relative = f"{directory}/{child.name}/{filename}"
                    output.append(
                        inspect_store(root, StoreSpec(relative, relative, limit))
                    )
    except OSError:
        status = "unavailable"
    return output, status


def storage_health(root: Path, *, max_accounts: int = 256) -> dict:
    if not 1 <= max_accounts <= 1000:
        raise ValueError("account inventory bound must be 1-1000")
    reports = [inspect_store(root, spec) for spec in DEFAULT_STORES]
    inventory = "complete"
    account_root = root / "accounting" / "accounts"
    if (
        root.is_symlink()
        or (root / "accounting").is_symlink()
        or account_root.is_symlink()
    ):
        inventory = "unavailable"
    elif account_root.exists():
        try:
            for index, path in enumerate(account_root.iterdir()):
                if index >= max_accounts:
                    inventory = "bounded_inventory_exhausted"
                    break
                if path.suffix == ".json":
                    reports.append(
                        inspect_store(
                            root,
                            StoreSpec(
                                f"account:{path.stem}",
                                f"accounting/accounts/{path.name}",
                                MAX_DOCUMENT_BYTES,
                            ),
                        )
                    )
        except OSError:
            inventory = "unavailable"
    paper, paper_inventory = _named_inventory(
        root,
        "paper",
        (
            ("account.json", MAX_DOCUMENT_BYTES),
            ("account.manifest.json", MAX_DOCUMENT_BYTES),
            ("account.audit.jsonl", MAX_LEDGER_BYTES),
        ),
        max_accounts,
    )
    artifacts, artifact_inventory = _named_inventory(
        root,
        "example-runs",
        (("launch.json", MAX_DOCUMENT_BYTES), ("result.json", 512 * 1024)),
        max_accounts,
    )
    reports.extend(paper + artifacts)
    # Custom CLI account/journal locations and other domain stores require their
    # own inventory contracts. Partial coverage cannot imply overall health.
    uninspected = ["custom_account_or_journal_paths", "other_application_stores"]
    return {
        "schema_version": 1,
        "scope": "point_in_time_file_sizes",
        "coverage": "partial",
        "integrity_checked": False,
        "account_inventory": inventory,
        "paper_inventory": paper_inventory,
        "artifact_inventory": artifact_inventory,
        "uninspected_inventories": uninspected,
        "attention_required": bool(uninspected)
        or inventory != "complete"
        or paper_inventory not in {"complete", "absent"}
        or artifact_inventory not in {"complete", "absent"}
        or any(
            row["status"]
            in {"near_limit", "limit_reached", "unavailable", "limit_unconfigured"}
            for row in reports
        ),
        "stores": reports,
    }
