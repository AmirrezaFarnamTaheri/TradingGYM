from dataclasses import dataclass


@dataclass(frozen=True)
class PromotionRecord:
    model_id: str
    state: str = "candidate"
    revision: int = 0
    evidence_sha256: str = ""


def promote(record, *, expected_revision, actor, approver, evidence_sha256):
    if record.revision != expected_revision:
        raise RuntimeError("revision_conflict")
    if actor == approver:
        raise PermissionError("independent_approval_required")
    if len(evidence_sha256) != 64:
        raise ValueError("evidence hash")
    if record.state != "candidate":
        raise ValueError("invalid state")
    return PromotionRecord(
        record.model_id, "production", record.revision + 1, evidence_sha256
    )
