"""Small model-promotion record; not a substitute for authenticated approval.

Callers must independently authenticate both principals, check their roles,
and verify the exact evidence bytes. This helper grants no live authority.
"""

from dataclasses import dataclass
import re

_EVIDENCE_HASH = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class PromotionRecord:
    model_id: str
    state: str = "candidate"
    revision: int = 0
    evidence_sha256: str = ""


def _principal(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 160
        or value != value.strip()
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
    ):
        raise ValueError("invalid promotion principal")
    return value.casefold()


def promote(record, *, expected_revision, actor, approver, evidence_sha256):
    if (
        type(expected_revision) is not int
        or expected_revision < 0
        or type(record.revision) is not int
    ):
        raise ValueError("invalid promotion revision")
    if record.revision != expected_revision:
        raise RuntimeError("revision_conflict")
    identity = _principal(actor)
    reviewer = _principal(approver)
    if identity == reviewer:
        raise PermissionError("independent_approval_required")
    if (
        not isinstance(evidence_sha256, str)
        or not _EVIDENCE_HASH.fullmatch(evidence_sha256)
    ):
        raise ValueError("invalid evidence hash")
    if record.state != "candidate":
        raise ValueError("invalid state")
    return PromotionRecord(
        record.model_id, "production", record.revision + 1, evidence_sha256
    )
