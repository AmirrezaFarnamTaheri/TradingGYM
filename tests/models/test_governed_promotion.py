import pytest

from trading_gym.models.promotion import PromotionRecord, promote


def test_model_promotion_requires_evidence_revision_and_independent_approval() -> None:
    record = PromotionRecord("model-a")
    with pytest.raises(PermissionError):
        promote(record, expected_revision=0, actor="alice", approver="alice", evidence_sha256="a" * 64)
    with pytest.raises(ValueError):
        promote(record, expected_revision=0, actor="alice", approver="bob", evidence_sha256="short")
    promoted = promote(record, expected_revision=0, actor="alice", approver="bob", evidence_sha256="a" * 64)
    assert promoted.state == "production" and promoted.revision == 1
