"""Terminal workspace, marketplace admission, and collaboration review evidence."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import require_aware, sha256_payload

MAX_REVIEW_RELATIONSHIPS = 2_000_000
MAX_MARKETPLACE_PERMISSION_LINKS = 2_000_000


class WidgetKind(str, Enum):
    MARKET = "market"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    EXECUTION = "execution"
    RESEARCH = "research"
    OPERATIONS = "operations"
    GOVERNANCE = "governance"


class TerminalWidget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    widget_id: Annotated[str, Field(min_length=2, max_length=128)]
    kind: WidgetKind
    title: Annotated[str, Field(min_length=1, max_length=240)]
    evidence_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    stale: bool = False
    authoritative: bool = False


class MarketplacePackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    package_id: Annotated[str, Field(min_length=2, max_length=128)]
    version: Annotated[str, Field(min_length=1, max_length=80)]
    manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    archive_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    signature_verified: bool
    license_id: Annotated[str, Field(min_length=2, max_length=120)]
    requested_permissions: list[Annotated[str, Field(min_length=2, max_length=120)]] = (
        Field(max_length=1000)
    )
    executable_code: bool = False

    @model_validator(mode="after")
    def validate_permissions(self) -> "MarketplacePackage":
        if len(self.requested_permissions) != len(set(self.requested_permissions)):
            raise ValueError("marketplace permissions must be unique")
        return self


class ReviewState(str, Enum):
    OPEN = "open"
    CHANGES_REQUESTED = "changes-requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    CLOSED = "closed"


class CollaborationReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: Annotated[str, Field(min_length=2, max_length=128)]
    subject_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    author: Annotated[str, Field(min_length=2, max_length=120)]
    reviewers: list[Annotated[str, Field(min_length=2, max_length=120)]] = Field(
        min_length=1, max_length=100
    )
    approvals: list[Annotated[str, Field(min_length=2, max_length=120)]] = Field(
        default_factory=list, max_length=100
    )
    unresolved_comment_ids: list[
        Annotated[str, Field(min_length=2, max_length=128)]
    ] = Field(default_factory=list, max_length=10_000)
    state: ReviewState
    updated_at: datetime

    @model_validator(mode="after")
    def validate_review(self) -> "CollaborationReview":
        require_aware(self.updated_at, "updated_at")
        if len(self.reviewers) != len(set(self.reviewers)):
            raise ValueError("reviewers must be unique")
        if len(self.approvals) != len(set(self.approvals)):
            raise ValueError("review approvals must be unique")
        if len(self.unresolved_comment_ids) != len(set(self.unresolved_comment_ids)):
            raise ValueError("review comment IDs must be unique")
        if self.author in self.approvals:
            raise ValueError("review author cannot approve their own work")
        if not set(self.approvals) <= set(self.reviewers):
            raise ValueError("approvals must belong to configured reviewers")
        return self


class ProductCollaborationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    widgets: list[TerminalWidget] = Field(max_length=10_000)
    packages: list[MarketplacePackage] = Field(max_length=10_000)
    reviews: list[CollaborationReview] = Field(max_length=100_000)
    allowed_marketplace_permissions: list[
        Annotated[str, Field(min_length=2, max_length=120)]
    ] = Field(max_length=1000)
    required_review_approvals: Annotated[int, Field(ge=1, le=100)] = 2
    maximum_review_age_seconds: Annotated[int, Field(ge=60, le=10 * 365 * 86400)] = (
        30 * 86400
    )
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_request(self) -> "ProductCollaborationRequest":
        require_aware(self.evaluated_at, "evaluated_at")
        for values, label in (
            ([item.widget_id for item in self.widgets], "widget"),
            (
                [f"{item.package_id}:{item.version}" for item in self.packages],
                "package",
            ),
            ([item.review_id for item in self.reviews], "review"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} IDs must be unique")
        if len(self.allowed_marketplace_permissions) != len(
            set(self.allowed_marketplace_permissions)
        ):
            raise ValueError("allowed marketplace permissions must be unique")
        review_links = sum(
            len(item.reviewers) + len(item.approvals) + len(item.unresolved_comment_ids)
            for item in self.reviews
        )
        if review_links > MAX_REVIEW_RELATIONSHIPS:
            raise ValueError("collaboration review relationship budget exceeded")
        if (
            sum(len(item.requested_permissions) for item in self.packages)
            > MAX_MARKETPLACE_PERMISSION_LINKS
        ):
            raise ValueError("marketplace permission-link budget exceeded")
        return self


class ProductCollaborationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    terminal_widget_counts: dict[str, int]
    stale_widget_ids: list[str]
    marketplace_admissible_packages: list[str]
    marketplace_rejected_packages: dict[str, list[str]]
    review_ready_ids: list[str]
    review_blocked_ids: list[str]
    blockers: list[str]
    warnings: list[str]
    report_sha256: str
    package_installation_authority_granted: bool = False
    review_state_mutation_performed: bool = False
    ui_authority_granted: bool = False


def evaluate_product_collaboration(
    request: ProductCollaborationRequest,
) -> ProductCollaborationReport:
    counts: dict[str, int] = {}
    stale: list[str] = []
    blockers: list[str] = []
    warnings: list[str] = []
    for widget in request.widgets:
        counts[widget.kind.value] = counts.get(widget.kind.value, 0) + 1
        if widget.stale:
            stale.append(widget.widget_id)
        if widget.authoritative:
            blockers.append(f"authoritative_ui_widget:{widget.widget_id}")
    admissible: list[str] = []
    rejected: dict[str, list[str]] = {}
    allowed = set(request.allowed_marketplace_permissions)
    forbidden = {
        "submit_order",
        "withdraw",
        "deploy",
        "shell",
        "arbitrary_code",
        "activate_model",
    }
    for package in request.packages:
        key = f"{package.package_id}:{package.version}"
        reasons = []
        requested = set(package.requested_permissions)
        if not package.signature_verified:
            reasons.append("signature_unverified")
        if package.executable_code:
            reasons.append("executable_package_requires_quarantine")
        if requested - allowed:
            reasons.append("permission_not_allowed")
        if requested & forbidden:
            reasons.append("forbidden_permission")
        if reasons:
            rejected[key] = sorted(set(reasons))
        else:
            admissible.append(key)
    ready: list[str] = []
    blocked: list[str] = []
    for review in request.reviews:
        age = (request.evaluated_at - review.updated_at).total_seconds()
        if age < 0:
            blockers.append(f"future_review:{review.review_id}")
            blocked.append(review.review_id)
            continue
        if age > request.maximum_review_age_seconds:
            blockers.append(f"stale_review:{review.review_id}")
            blocked.append(review.review_id)
            continue
        if (
            review.state == ReviewState.APPROVED
            and len(set(review.approvals)) >= request.required_review_approvals
            and not review.unresolved_comment_ids
        ):
            ready.append(review.review_id)
        else:
            blocked.append(review.review_id)
    if stale:
        warnings.append("stale_terminal_widgets")
    if rejected:
        warnings.append("marketplace_packages_rejected")
    if blocked:
        warnings.append("reviews_not_ready")
    payload = {
        "terminal_widget_counts": dict(sorted(counts.items())),
        "stale_widget_ids": sorted(stale),
        "marketplace_admissible_packages": sorted(admissible),
        "marketplace_rejected_packages": dict(sorted(rejected.items())),
        "review_ready_ids": sorted(ready),
        "review_blocked_ids": sorted(blocked),
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    return ProductCollaborationReport(**payload, report_sha256=sha256_payload(payload))
