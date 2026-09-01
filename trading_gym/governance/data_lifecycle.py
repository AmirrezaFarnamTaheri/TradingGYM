"""Bounded, non-destructive data-retention and legal-hold evaluation.

This plane classifies durable assets against explicit retention rules and legal
holds.  It produces reviewable retain/review/purge-candidate decisions but has
no filesystem, database, deletion, archival, or legal authority.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,199}$"
_CATEGORY = r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$"


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class LifecycleAction(str, Enum):
    RETAIN = "retain"
    REVIEW = "review"
    PURGE_CANDIDATE = "purge_candidate"
    BLOCKED_BY_HOLD = "blocked_by_hold"


class DataAssetEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: Annotated[str, Field(pattern=_ID)]
    category: Annotated[str, Field(pattern=_CATEGORY)]
    classification: DataClassification
    created_at: datetime
    updated_at: datetime
    size_bytes: Annotated[int, Field(ge=0, le=20 * 1024**4)]
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    contains_personal_data: bool = False
    immutable_evidence: bool = False

    @model_validator(mode="after")
    def validate_asset(self) -> "DataAssetEvidence":
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


class RetentionRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Annotated[str, Field(pattern=_CATEGORY)]
    classification: DataClassification
    review_after_days: Annotated[int, Field(ge=1, le=36_500)]
    purge_after_days: Annotated[int | None, Field(default=None, ge=1, le=36_500)]
    personal_data_allowed: bool = False
    immutable_evidence_must_be_retained: bool = True

    @model_validator(mode="after")
    def validate_rule(self) -> "RetentionRule":
        if self.purge_after_days is not None and self.purge_after_days < self.review_after_days:
            raise ValueError("purge_after_days must be at least review_after_days")
        return self


class LegalHoldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hold_id: Annotated[str, Field(pattern=_ID)]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    starts_at: datetime
    expires_at: datetime | None = None
    asset_ids: list[Annotated[str, Field(pattern=_ID)]] = Field(default_factory=list, max_length=10_000)
    categories: list[Annotated[str, Field(pattern=_CATEGORY)]] = Field(default_factory=list, max_length=1000)
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def validate_hold(self) -> "LegalHoldEvidence":
        _aware(self.starts_at, "starts_at")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
            if self.expires_at <= self.starts_at:
                raise ValueError("expires_at must be after starts_at")
        if not self.asset_ids and not self.categories:
            raise ValueError("a legal hold must target at least one asset or category")
        if len(set(self.asset_ids)) != len(self.asset_ids) or len(set(self.categories)) != len(self.categories):
            raise ValueError("legal hold targets must be unique")
        return self


class DataLifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    assets: list[DataAssetEvidence] = Field(max_length=100_000)
    rules: list[RetentionRule] = Field(max_length=1000)
    legal_holds: list[LegalHoldEvidence] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def validate_request(self) -> "DataLifecycleRequest":
        _aware(self.evaluated_at, "evaluated_at")
        return self


class DataLifecycleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str
    category: str
    action: LifecycleAction
    age_days: int = Field(ge=0)
    review_eligible_at: datetime | None = None
    purge_eligible_at: datetime | None = None
    active_hold_ids: list[str]
    reasons: list[str]


class DataLifecycleReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    decisions: list[DataLifecycleDecision]
    counts: dict[str, int]
    blockers: list[str]
    warnings: list[str]
    ready_for_human_review: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    deletion_authority_granted: bool = False
    archive_authority_granted: bool = False
    legal_authority_granted: bool = False
    mutation_performed: bool = False


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def evaluate_data_lifecycle(request: DataLifecycleRequest) -> DataLifecycleReport:
    blockers: list[str] = []
    warnings: list[str] = []
    decisions: list[DataLifecycleDecision] = []

    asset_ids = [item.asset_id for item in request.assets]
    if len(set(asset_ids)) != len(asset_ids):
        blockers.append("duplicate_asset_id")
    rule_categories = [item.category for item in request.rules]
    if len(set(rule_categories)) != len(rule_categories):
        blockers.append("duplicate_retention_rule")
    hold_ids = [item.hold_id for item in request.legal_holds]
    if len(set(hold_ids)) != len(hold_ids):
        blockers.append("duplicate_legal_hold_id")

    rules = {item.category: item for item in request.rules}
    active_holds = [
        item
        for item in request.legal_holds
        if item.starts_at <= request.evaluated_at
        and (item.expires_at is None or request.evaluated_at < item.expires_at)
    ]

    for asset in sorted(request.assets, key=lambda item: item.asset_id):
        reasons: list[str] = []
        holds = sorted(
            item.hold_id
            for item in active_holds
            if asset.asset_id in item.asset_ids or asset.category in item.categories
        )
        rule = rules.get(asset.category)
        age_days = max(0, (request.evaluated_at - asset.created_at).days)
        if asset.created_at > request.evaluated_at or asset.updated_at > request.evaluated_at:
            blockers.append(f"asset_timestamp_in_future:{asset.asset_id}")
        if rule is None:
            blockers.append(f"retention_rule_missing:{asset.asset_id}")
            decisions.append(DataLifecycleDecision(
                asset_id=asset.asset_id,
                category=asset.category,
                action=LifecycleAction.REVIEW,
                age_days=age_days,
                active_hold_ids=holds,
                reasons=["no_explicit_retention_rule"],
            ))
            continue
        if rule.classification is not asset.classification:
            blockers.append(f"classification_rule_mismatch:{asset.asset_id}")
            reasons.append("classification_rule_mismatch")
        if asset.contains_personal_data and not rule.personal_data_allowed:
            blockers.append(f"personal_data_not_allowed:{asset.asset_id}")
            reasons.append("personal_data_not_allowed_by_rule")

        review_at = asset.created_at + timedelta(days=rule.review_after_days)
        purge_at = (
            asset.created_at + timedelta(days=rule.purge_after_days)
            if rule.purge_after_days is not None
            else None
        )
        if holds:
            action = LifecycleAction.BLOCKED_BY_HOLD
            reasons.append("active_legal_hold")
        elif asset.immutable_evidence and rule.immutable_evidence_must_be_retained:
            action = LifecycleAction.RETAIN
            reasons.append("immutable_evidence_retention")
            if purge_at is not None and request.evaluated_at >= purge_at:
                warnings.append(f"immutable_evidence_past_purge_age:{asset.asset_id}")
        elif purge_at is not None and request.evaluated_at >= purge_at:
            action = LifecycleAction.PURGE_CANDIDATE
            reasons.append("retention_period_elapsed")
        elif request.evaluated_at >= review_at:
            action = LifecycleAction.REVIEW
            reasons.append("review_period_elapsed")
        else:
            action = LifecycleAction.RETAIN
            reasons.append("within_retention_period")

        decisions.append(DataLifecycleDecision(
            asset_id=asset.asset_id,
            category=asset.category,
            action=action,
            age_days=age_days,
            review_eligible_at=review_at,
            purge_eligible_at=purge_at,
            active_hold_ids=holds,
            reasons=sorted(set(reasons)),
        ))

    counts = {action.value: sum(item.action is action for item in decisions) for action in LifecycleAction}
    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    payload = {
        "evaluated_at": request.evaluated_at.isoformat(),
        "decisions": [item.model_dump(mode="json") for item in decisions],
        "counts": counts,
        "blockers": blockers,
        "warnings": warnings,
    }
    return DataLifecycleReport(
        evaluated_at=request.evaluated_at,
        decisions=decisions,
        counts=counts,
        blockers=blockers,
        warnings=warnings,
        ready_for_human_review=not blockers,
        report_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
    )
