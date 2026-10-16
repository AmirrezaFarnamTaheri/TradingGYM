"""Model promotion evidence and principal identity safety regressions."""

import pytest

from trading_gym.models.promotion import PromotionRecord, promote


@pytest.mark.parametrize("digest", ["z" * 64, "A" * 64, " " * 64])
def test_noncanonical_or_nonhex_evidence_hash_is_rejected(digest):
    with pytest.raises(ValueError, match="evidence hash"):
        promote(
            PromotionRecord("model-a"),
            expected_revision=0,
            actor="alice",
            approver="bob",
            evidence_sha256=digest,
        )


@pytest.mark.parametrize(
    "actor,approver",
    [
        ("", "bob"),
        ("alice", " "),
        ("alice", "ALICE"),
        (" alice", "alice"),
        ("alice ", "bob"),
    ],
)
def test_blank_or_identity_alias_reviewers_cannot_promote(actor, approver):
    with pytest.raises((ValueError, PermissionError)):
        promote(
            PromotionRecord("model-a"),
            expected_revision=0,
            actor=actor,
            approver=approver,
            evidence_sha256="a" * 64,
        )


def test_boolean_revision_cannot_pass_optimistic_promotion_lock():
    with pytest.raises(ValueError, match="revision"):
        promote(
            PromotionRecord("model-a", revision=1),
            expected_revision=True,
            actor="alice",
            approver="bob",
            evidence_sha256="a" * 64,
        )
