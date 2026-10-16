"""Read-only intake of one bounded skill manifest file; never imports code."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

_MAX_PACKAGE_BYTES = 16_777_216
_MARKERS = (
    "subprocess", "os.system", "requests.", "http://", "https://",
    "private_key", "mnemonic",
)
_SECRET = re.compile(
    r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{40,}"
    r"|\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}",
)


def inspect_skill_package(path):
    p = Path(path)
    if p.is_symlink():
        raise ValueError("skill package path cannot be a symlink")
    # O_NOFOLLOW closes the final-component TOCTOU window on POSIX.
    try:
        descriptor = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ValueError("skill package unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_PACKAGE_BYTES:
            raise ValueError("skill package is not a bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(_MAX_PACKAGE_BYTES + 1)
        if len(data) > _MAX_PACKAGE_BYTES:
            raise ValueError("skill package byte budget exceeded")
    finally:
        os.close(descriptor)
    text = data.decode("utf-8", "replace")
    lower = text.lower()
    markers = [marker for marker in _MARKERS if marker in lower]
    if _SECRET.search(text):
        markers.append("embedded_secret")
    return {
        "path": p.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "risk_markers": sorted(set(markers)),
        "quarantined": True,
        "activated": False,
    }
