from datetime import datetime, timedelta, timezone

from trading_gym.execution.governance.data_lifecycle import (
    DataAssetEvidence,
    DataClassification,
    DataLifecycleRequest,
    LegalHoldEvidence,
    LifecycleAction,
    RetentionRule,
    evaluate_data_lifecycle,
)


def asset(
    *, asset_id="dataset:prices-001", days_old=100, immutable=False, personal=False
):
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    return DataAssetEvidence(
        asset_id=asset_id,
        category="market-data",
        classification=DataClassification.CONFIDENTIAL,
        created_at=now - timedelta(days=days_old),
        updated_at=now - timedelta(days=1),
        size_bytes=100,
        content_sha256="a" * 64,
        contains_personal_data=personal,
        immutable_evidence=immutable,
    )


def rule(**updates):
    values = dict(
        category="market-data",
        classification=DataClassification.CONFIDENTIAL,
        review_after_days=30,
        purge_after_days=90,
        personal_data_allowed=False,
    )
    values.update(updates)
    return RetentionRule(**values)


def test_elapsed_asset_becomes_purge_candidate_without_mutation_authority():
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    report = evaluate_data_lifecycle(
        DataLifecycleRequest(
            evaluated_at=now,
            assets=[asset()],
            rules=[rule()],
            legal_holds=[],
        )
    )
    assert report.ready_for_human_review is True
    assert report.decisions[0].action is LifecycleAction.PURGE_CANDIDATE
    assert report.deletion_authority_granted is False
    assert report.mutation_performed is False


def test_active_legal_hold_blocks_purge_candidate():
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    report = evaluate_data_lifecycle(
        DataLifecycleRequest(
            evaluated_at=now,
            assets=[asset()],
            rules=[rule()],
            legal_holds=[
                LegalHoldEvidence(
                    hold_id="hold:case-001",
                    reason="open investigation",
                    starts_at=now - timedelta(days=1),
                    asset_ids=["dataset:prices-001"],
                    source_sha256="b" * 64,
                )
            ],
        )
    )
    assert report.decisions[0].action is LifecycleAction.BLOCKED_BY_HOLD
    assert report.decisions[0].active_hold_ids == ["hold:case-001"]


def test_missing_rule_and_disallowed_personal_data_fail_closed():
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    report = evaluate_data_lifecycle(
        DataLifecycleRequest(
            evaluated_at=now,
            assets=[
                asset(asset_id="dataset:personal-001", personal=True),
                asset(asset_id="dataset:other-001"),
            ],
            rules=[rule(category="other-category")],
        )
    )
    assert report.ready_for_human_review is False
    assert any(item.startswith("retention_rule_missing") for item in report.blockers)


def test_immutable_evidence_is_retained_after_purge_age():
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)
    report = evaluate_data_lifecycle(
        DataLifecycleRequest(
            evaluated_at=now,
            assets=[asset(immutable=True)],
            rules=[rule()],
        )
    )
    assert report.decisions[0].action is LifecycleAction.RETAIN
    assert "immutable_evidence_past_purge_age:dataset:prices-001" in report.warnings
