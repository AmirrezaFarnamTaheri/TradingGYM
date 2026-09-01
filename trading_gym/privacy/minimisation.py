"""Evidence-only privacy minimisation and data-subject request planning."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Classification = Literal["public", "internal", "confidential", "personal", "sensitive_personal", "secret"]


class DataFieldRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field_id: Annotated[str, Field(min_length=1, max_length=200)]
    store_id: Annotated[str, Field(min_length=1, max_length=160)]
    classification: Classification
    purpose: Annotated[str, Field(min_length=1, max_length=500)]
    retention_days: int = Field(ge=0, le=36_500)
    collected_at: datetime
    last_accessed_at: datetime | None = None
    encrypted_at_rest: bool = False
    access_roles: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(max_length=100)
    subject_linkable: bool = False
    legal_hold: bool = False
    required_for_operation: bool = True

    @model_validator(mode="after")
    def validate_times(self) -> "DataFieldRecord":
        for name in ("collected_at", "last_accessed_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if self.last_accessed_at is not None and self.last_accessed_at < self.collected_at:
            raise ValueError("last_accessed_at precedes collected_at")
        if len(set(self.access_roles)) != len(self.access_roles):
            raise ValueError("access_roles contains duplicates")
        return self


class PrivacyAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluated_at: datetime
    fields: list[DataFieldRecord] = Field(max_length=100_000)
    subject_id: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    maximum_sensitive_retention_days: int = Field(default=365, ge=1, le=36_500)

    @model_validator(mode="after")
    def aware(self) -> "PrivacyAssessmentRequest":
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        return self


class SubjectRequestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_id: str | None
    searchable_fields: list[str]
    held_fields: list[str]
    deletable_candidates: list[str]
    manual_review_fields: list[str]
    mutation_performed: bool = False
    deletion_authority_granted: bool = False


class PrivacyAssessmentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field_count: int
    expired_field_ids: list[str]
    minimisation_candidates: list[str]
    encryption_gaps: list[str]
    excessive_access_fields: list[str]
    subject_request_plan: SubjectRequestPlan
    blockers: list[str]
    warnings: list[str]
    legal_conclusion_reached: bool = False
    mutation_performed: bool = False


def evaluate_privacy(request: PrivacyAssessmentRequest) -> PrivacyAssessmentReport:
    blockers: list[str] = []
    warnings: list[str] = []
    expired: list[str] = []
    minimise: list[str] = []
    encryption: list[str] = []
    excessive_access: list[str] = []
    searchable: list[str] = []
    held: list[str] = []
    deletable: list[str] = []
    manual: list[str] = []
    seen: set[str] = set()
    for item in request.fields:
        if item.field_id in seen:
            blockers.append(f"duplicate_field:{item.field_id}")
            continue
        seen.add(item.field_id)
        age = request.evaluated_at - item.collected_at
        is_expired = age > timedelta(days=item.retention_days)
        if is_expired:
            expired.append(item.field_id)
            if item.legal_hold:
                held.append(item.field_id)
            elif item.required_for_operation:
                manual.append(item.field_id)
            else:
                deletable.append(item.field_id)
        if not item.required_for_operation:
            minimise.append(item.field_id)
        if item.classification in {"personal", "sensitive_personal", "secret"} and not item.encrypted_at_rest:
            encryption.append(item.field_id)
            blockers.append(f"encryption_required:{item.field_id}")
        if item.classification == "sensitive_personal" and item.retention_days > request.maximum_sensitive_retention_days:
            blockers.append(f"sensitive_retention_exceeded:{item.field_id}")
        if item.classification in {"personal", "sensitive_personal", "secret"} and len(item.access_roles) > 10:
            excessive_access.append(item.field_id)
        if item.subject_linkable:
            searchable.append(item.field_id)
    if expired:
        warnings.append(f"expired_fields:{len(expired)}")
    if minimise:
        warnings.append(f"minimisation_candidates:{len(minimise)}")
    return PrivacyAssessmentReport(
        field_count=len(request.fields),
        expired_field_ids=sorted(expired),
        minimisation_candidates=sorted(minimise),
        encryption_gaps=sorted(encryption),
        excessive_access_fields=sorted(excessive_access),
        subject_request_plan=SubjectRequestPlan(
            subject_id=request.subject_id,
            searchable_fields=sorted(searchable),
            held_fields=sorted(held),
            deletable_candidates=sorted(deletable),
            manual_review_fields=sorted(manual),
        ),
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
    )
