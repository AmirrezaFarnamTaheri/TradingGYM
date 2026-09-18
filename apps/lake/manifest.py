"""Crash-safe, path-confined integrity manifests for parquet lake partitions."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from trading_gym.common.persistence import atomic_write_json, read_json
from trading_gym.common.security import confined_child

SCHEMA_VERSION = 2
MAX_PARTITION_FILES = 100_000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_PARQUET_BYTES = 20 * 1024**3
_PARTITION_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,199}$")
_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._=-]{0,239}\.parquet$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _hash_file(path: Path) -> str:
    """Compute SHA-256 without following untrusted symlinks."""

    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Lake artifact must be a regular file: {path.name}")
    size = path.stat().st_size
    if size > MAX_PARQUET_BYTES:
        raise ValueError(
            f"Lake artifact exceeds {MAX_PARQUET_BYTES} bytes: {path.name}"
        )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_partition_name(value: str) -> str:
    """Validate a bounded POSIX-relative partition identifier.

    Schema v2 originally accepted only one directory name.  Production lake
    layout is ``symbol/timeframe``; supporting safe nested identifiers avoids
    collisions such as ``BTC/5m`` and ``ETH/5m`` while retaining compatibility
    with existing one-level manifests.
    """
    raw = str(value).replace("\\", "/").strip("/")
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or len(path.parts) > 8
        or any(
            part in {"", ".", ".."} or not _PARTITION_SEGMENT.fullmatch(part)
            for part in path.parts
        )
    ):
        raise ValueError("Partition name is unsafe")
    return path.as_posix()


def _validate_file_name(value: str) -> str:
    if not _FILE_NAME.fullmatch(value) or Path(value).name != value:
        raise ValueError("Manifest contains an unsafe parquet filename")
    return value


def _normalise_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported lake manifest schema")
    partition = _validate_partition_name(str(value.get("partition", "")))
    raw_files = value.get("files")
    if not isinstance(raw_files, dict) or len(raw_files) > MAX_PARTITION_FILES:
        raise ValueError("Lake manifest files mapping is invalid or unbounded")
    files: dict[str, dict[str, Any]] = {}
    for raw_name, raw_record in raw_files.items():
        name = _validate_file_name(str(raw_name))
        if not isinstance(raw_record, dict):
            raise ValueError("Lake manifest file record is invalid")
        digest = str(raw_record.get("sha256", ""))
        size = raw_record.get("size_bytes")
        if not _HEX64.fullmatch(digest):
            raise ValueError("Lake manifest contains an invalid SHA-256")
        if not isinstance(size, int) or not 0 <= size <= MAX_PARQUET_BYTES:
            raise ValueError("Lake manifest contains an invalid file size")
        files[name] = {"sha256": digest, "size_bytes": size}
    return {"schema_version": SCHEMA_VERSION, "partition": partition, "files": files}


def _leaf_hash(partition: str, filename: str, record: dict[str, Any]) -> str:
    material = (
        partition.encode("utf-8")
        + b"\0"
        + filename.encode("utf-8")
        + b"\0"
        + record["sha256"].encode("ascii")
        + b"\0"
        + str(record["size_bytes"]).encode("ascii")
    )
    return hashlib.sha256(material).hexdigest()


def _merkle_tree(hashes: list[str]) -> str:
    """Compute a deterministic binary Merkle root over hexadecimal leaf hashes."""

    if not hashes:
        return hashlib.sha256(b"").hexdigest()
    level = list(hashes)
    while len(level) > 1:
        next_level: list[str] = []
        for index in range(0, len(level), 2):
            left = level[index]
            right = level[index + 1] if index + 1 < len(level) else left
            next_level.append(
                hashlib.sha256(bytes.fromhex(left) + bytes.fromhex(right)).hexdigest()
            )
        level = next_level
    return level[0]


def _manifest_paths(
    manifests_dir: Path, merkle_root_path: Path | None = None
) -> list[Path]:
    if manifests_dir.is_symlink() or not manifests_dir.is_dir():
        raise ValueError("Manifest directory must be a regular directory")
    paths = []
    for path in sorted(manifests_dir.glob("*.manifest.json")):
        if merkle_root_path is not None and path.resolve(
            strict=False
        ) == merkle_root_path.resolve(strict=False):
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("Manifest paths must be regular files")
        paths.append(path)
    if len(paths) > MAX_PARTITION_FILES:
        raise ValueError("Manifest count exceeds safety limit")
    return paths


def _load_manifests(paths: Iterable[Path]) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        manifest = _normalise_manifest(
            read_json(path, {}, max_bytes=MAX_MANIFEST_BYTES)
        )
        partition = manifest["partition"]
        if partition in seen:
            raise ValueError(f"Duplicate partition manifest: {partition}")
        seen.add(partition)
        manifests.append(manifest)
    return manifests


def write_partition_manifest(
    partition_dir: Path,
    manifest_path: Path,
    *,
    partition_id: str | None = None,
) -> None:
    """Write a complete manifest for one partition using an atomic replacement.

    ``partition_id`` should be the partition path relative to the lake root
    (for example ``BTC/5m``).  Omitting it preserves legacy one-level behavior.
    """

    partition_dir = partition_dir.expanduser()
    if partition_dir.is_symlink() or not partition_dir.is_dir():
        raise ValueError("Partition must be a regular directory")
    partition = _validate_partition_name(partition_id or partition_dir.name)
    files = sorted(partition_dir.glob("*.parquet"), key=lambda item: item.name)
    if len(files) > MAX_PARTITION_FILES:
        raise ValueError("Partition file count exceeds safety limit")
    records: dict[str, dict[str, Any]] = {}
    for file_path in files:
        name = _validate_file_name(file_path.name)
        size = file_path.stat().st_size if not file_path.is_symlink() else -1
        records[name] = {"sha256": _hash_file(file_path), "size_bytes": size}
    atomic_write_json(
        manifest_path,
        {"schema_version": SCHEMA_VERSION, "partition": partition, "files": records},
    )


def write_daily_merkle(manifests_dir: Path, merkle_root_path: Path) -> None:
    """Bind every partition, filename, size, and content hash into one root."""

    manifests = _load_manifests(_manifest_paths(manifests_dir, merkle_root_path))
    leaves = [
        _leaf_hash(manifest["partition"], filename, record)
        for manifest in sorted(manifests, key=lambda item: item["partition"])
        for filename, record in sorted(manifest["files"].items())
    ]
    atomic_write_json(
        merkle_root_path,
        {
            "schema_version": SCHEMA_VERSION,
            "manifest_count": len(manifests),
            "file_count": len(leaves),
            "merkle_root": _merkle_tree(leaves),
        },
    )


def verify_lake(lake_dir: Path, manifests_dir: Path, merkle_root_path: Path) -> bool:
    """Verify manifests, exact partition membership, and the aggregate root."""

    try:
        lake_dir = lake_dir.expanduser().resolve(strict=True)
        if lake_dir.is_symlink() or not lake_dir.is_dir():
            return False
        manifests = _load_manifests(_manifest_paths(manifests_dir, merkle_root_path))
        leaves: list[str] = []
        for manifest in sorted(manifests, key=lambda item: item["partition"]):
            partition = manifest["partition"]
            partition_dir = confined_child(
                lake_dir,
                *PurePosixPath(partition).parts,
                must_exist=True,
                allow_symlinks=False,
            )
            if not partition_dir.is_dir() or partition_dir.is_symlink():
                return False
            expected_names = set(manifest["files"])
            actual_files = {
                path.name: path
                for path in partition_dir.glob("*.parquet")
                if path.is_file() and not path.is_symlink()
            }
            if set(actual_files) != expected_names:
                return False
            for filename, record in sorted(manifest["files"].items()):
                file_path = confined_child(
                    partition_dir, filename, must_exist=True, allow_symlinks=False
                )
                if file_path.stat().st_size != record["size_bytes"]:
                    return False
                if _hash_file(file_path) != record["sha256"]:
                    return False
                leaves.append(_leaf_hash(partition, filename, record))

        root = read_json(merkle_root_path, {}, max_bytes=64 * 1024)
        if not isinstance(root, dict) or root.get("schema_version") != SCHEMA_VERSION:
            return False
        if root.get("manifest_count") != len(manifests) or root.get(
            "file_count"
        ) != len(leaves):
            return False
        actual_root = root.get("merkle_root")
        return isinstance(actual_root, str) and actual_root == _merkle_tree(leaves)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return False
