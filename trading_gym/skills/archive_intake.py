"""Read-only ZIP skill intake with bounded recursive member inspection.

No extraction, imports, code execution, installation, or capability grants.
A digest only binds observed bytes; it does not authenticate a publisher.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
from zipfile import BadZipFile, LargeZipFile, ZipFile

_MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_MEMBERS = 2048
_MAX_RATIO = 200
_MANIFEST_NAMES = frozenset({"skill.md", "skill.json", "manifest.json"})
_EXECUTABLE_SUFFIXES = frozenset(
    {".sh", ".ps1", ".bat", ".cmd", ".exe", ".dll", ".so", ".dylib"}
)
_NESTED_SUFFIXES = frozenset({".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"})
_RISK_MARKERS = (
    "subprocess",
    "os.system",
    "requests.",
    "http://",
    "https://",
    "private_key",
    "mnemonic",
)
_SECRET = re.compile(
    r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{40,}"
    r"|\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}"
)


def _safe_member_path(name: str) -> tuple[str, bool]:
    """Reject names that can escape or alias folders on common platforms."""
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 512
        or name.startswith("/")
        or "\\" in name
        or ":" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise ValueError("unsafe skill archive member path")
    is_directory = name.endswith("/")
    parts = name[:-1].split("/") if is_directory else name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("unsafe skill archive member path")
    if any(len(part) > 255 or part.endswith((" ", ".")) for part in parts):
        raise ValueError("unsafe skill archive member path")
    return unicodedata.normalize("NFC", "/".join(parts)), is_directory


def _read_package(path: Path) -> bytes:
    if path.is_symlink():
        raise ValueError("skill archive path must not be a symlink")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ValueError("skill archive unavailable") from exc
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 0 < metadata.st_size <= _MAX_ARCHIVE_BYTES
        ):
            raise ValueError("skill archive size or type budget exceeded")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            blob = stream.read(_MAX_ARCHIVE_BYTES + 1)
        if len(blob) > _MAX_ARCHIVE_BYTES or len(blob) != metadata.st_size:
            raise ValueError("skill archive read budget exceeded")
        return blob
    finally:
        os.close(fd)


def inspect_skill_archive(path: str | Path) -> dict[str, object]:
    """Return nested file hashes and static risk markers, always quarantined.

    Nested archives are flagged but not recursively unpacked; safe admission
    requires a separate review of each embedded archive. The full ZIP and
    each decoded member have strict byte budgets and aliases are forbidden.
    """
    archive_path = Path(path)
    blob = _read_package(archive_path)
    all_markers: set[str] = set()
    seen: set[str] = set()
    folders: set[str] = set()
    files: list[dict[str, object]] = []
    found_manifest = False
    try:
        with ZipFile(BytesIO(blob)) as archive:
            members = archive.infolist()
            if not members or len(members) > _MAX_MEMBERS:
                raise ValueError("skill archive member-count budget exceeded")
            total_size = 0
            for member in members:
                safe_name, is_directory = _safe_member_path(member.filename)
                identity = safe_name.casefold()
                if identity in seen:
                    raise ValueError("duplicate skill archive member path")
                seen.add(identity)
                mode = (member.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise ValueError("skill archive symlink member denied")
                file_type = stat.S_IFMT(mode)
                if file_type not in (0, stat.S_IFDIR if is_directory else stat.S_IFREG):
                    raise ValueError("skill archive member type denied")
                if bool(member.is_dir()) != is_directory:
                    raise ValueError("skill archive directory type mismatch")
                if member.flag_bits & 1:
                    raise ValueError("encrypted skill archive member denied")
                if member.file_size < 0 or member.compress_size < 0:
                    raise ValueError("invalid skill archive member sizes")
                parts = safe_name.split("/")
                for count in range(1, len(parts) + (1 if is_directory else 0)):
                    folders.add("/".join(parts[:count]))
                if is_directory:
                    if member.file_size:
                        raise ValueError("skill archive directory has payload")
                    continue
                total_size += member.file_size
                if member.file_size > _MAX_FILE_BYTES or total_size > _MAX_TOTAL_BYTES:
                    raise ValueError("skill archive uncompressed size budget exceeded")
                if member.file_size and (
                    not member.compress_size
                    or member.file_size > _MAX_RATIO * member.compress_size
                ):
                    raise ValueError(
                        "skill archive decompression ratio budget exceeded"
                    )
                with archive.open(member, "r") as stream:
                    content = stream.read(_MAX_FILE_BYTES + 1)
                if len(content) != member.file_size:
                    raise ValueError("skill archive member size mismatch")
                text = content.decode("utf-8", "replace")
                lower = text.lower()
                markers = {word for word in _RISK_MARKERS if word in lower}
                if _SECRET.search(text):
                    markers.add("embedded_secret")
                if b"\x00" in content:
                    markers.add("binary_content")
                suffix = Path(safe_name).suffix.casefold()
                if suffix in _EXECUTABLE_SUFFIXES or (mode & 0o111):
                    markers.add("executable_payload")
                if suffix in _NESTED_SUFFIXES:
                    markers.add("nested_archive")
                all_markers.update(markers)
                if parts[-1].casefold() in _MANIFEST_NAMES:
                    found_manifest = True
                files.append(
                    {
                        "path": safe_name,
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size_bytes": len(content),
                        "risk_markers": sorted(markers),
                    }
                )
    except (BadZipFile, LargeZipFile, EOFError, RuntimeError, OSError) as exc:
        raise ValueError("skill archive decoding failed") from exc
    if not found_manifest:
        raise ValueError("skill archive manifest missing")
    files.sort(key=lambda item: item["path"])
    report: dict[str, object] = {
        "schema": "tradinggym.skill-archive-intake.v1",
        "path": archive_path.name,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "entry_count": len(files),
        "folders": sorted(folders),
        "files": files,
        "risk_markers": sorted(all_markers),
        "quarantined": True,
        "activated": False,
        "executed": False,
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return report
