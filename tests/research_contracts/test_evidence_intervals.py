"""A new artifact hash cannot make an already consumed market window unseen."""

import pytest

from trading_gym.rl.pipelines.research_protocol import audit_evidence_roles


def use(procedure, partition, role, start, end, *, scope="venue:BTC-USDT"):
    return {
        "procedure_id": procedure,
        "partition_sha256": partition,
        "role": role,
        "market_scope": scope,
        "period_start": start,
        "period_end": end,
        "available_by": 100.0,
        "consumed_at": 110.0,
    }


def test_different_partition_hash_does_not_hide_overlap_with_selection():
    result = audit_evidence_roles(
        [
            use("candidate", "first-hash", "selection", 0, 20),
            use("candidate", "renamed-hash", "final", 10, 30),
        ]
    )
    assert not result["valid"]
    assert "final_period_already_used:1" in result["blockers"]


def test_renamed_final_window_cannot_be_reused_by_another_procedure():
    result = audit_evidence_roles(
        [
            use("first", "original-hash", "final", 10, 30),
            use("second", "new-hash", "final", 20, 40),
        ]
    )
    assert not result["valid"]
    assert "final_period_already_used:1" in result["blockers"]


def test_adjacent_windows_and_distinct_market_scopes_are_not_overlaps():
    result = audit_evidence_roles(
        [
            use("first", "history", "selection", 0, 20),
            use("first", "final", "final", 20, 30),
            use("second", "other-market", "final", 0, 30, scope="venue:ETH-USDT"),
        ]
    )
    assert result["valid"]
    assert result["period_overlap_checked"]
    assert not result["statistical_independence_established"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("period_start", True),
        ("period_end", float("nan")),
        ("period_end", 0),
        ("market_scope", ""),
        ("available_by", 10),
    ],
)
def test_invalid_or_impossible_interval_metadata_is_rejected(field, value):
    record = use("candidate", "history", "selection", 0, 20)
    record[field] = value
    with pytest.raises(ValueError):
        audit_evidence_roles([record])


def test_missing_interval_cannot_claim_complete_holdout_audit():
    record = use("candidate", "history", "selection", 0, 20)
    del record["period_end"]
    with pytest.raises(ValueError, match="period"):
        audit_evidence_roles([record])


def test_reordering_consumption_records_cannot_hide_prior_use():
    earlier = use("development", "history", "selection", 0, 20)
    final = use("new-procedure", "renamed", "final", 0, 20)
    final["consumed_at"] = 120.0
    with pytest.raises(ValueError, match="chronological"):
        audit_evidence_roles([final, earlier])
