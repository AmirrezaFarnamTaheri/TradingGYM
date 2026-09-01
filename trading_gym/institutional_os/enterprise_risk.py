"""Enterprise market, credit, operational risk aggregation and explanations."""
from __future__ import annotations

import math
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import require_decimal, sha256_payload


class RiskDomain(str, Enum):
    MARKET = "market"
    CREDIT = "credit"
    LIQUIDITY = "liquidity"
    OPERATIONAL = "operational"
    MODEL = "model"
    COMPLIANCE = "compliance"


class RiskExposure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exposure_id: Annotated[str, Field(min_length=2, max_length=128)]
    domain: RiskDomain
    gross_loss_base: Decimal
    probability: float = Field(ge=0.0, le=1.0)
    correlation_group: Annotated[str, Field(min_length=1, max_length=120)]
    mitigated_fraction: Decimal = Decimal("0")
    owner: Annotated[str, Field(min_length=2, max_length=120)]

    @model_validator(mode="after")
    def validate_values(self) -> "RiskExposure":
        require_decimal(self.gross_loss_base, "gross_loss_base", minimum=Decimal("0"), maximum=Decimal("1e30"))
        require_decimal(self.mitigated_fraction, "mitigated_fraction", minimum=Decimal("0"), maximum=Decimal("1"))
        return self


class EnterpriseRiskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_expected_loss_base: Decimal
    maximum_single_exposure_base: Decimal
    maximum_domain_fraction: Decimal = Decimal("0.5")
    maximum_unowned_exposures: Annotated[int, Field(ge=0, le=100_000)] = 0

    @model_validator(mode="after")
    def validate_policy(self) -> "EnterpriseRiskPolicy":
        require_decimal(self.maximum_expected_loss_base, "maximum_expected_loss_base", minimum=Decimal("0"), maximum=Decimal("1e30"))
        require_decimal(self.maximum_single_exposure_base, "maximum_single_exposure_base", minimum=Decimal("0"), maximum=Decimal("1e30"))
        require_decimal(self.maximum_domain_fraction, "maximum_domain_fraction", minimum=Decimal("0"), maximum=Decimal("1"))
        return self


class EnterpriseRiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exposures: list[RiskExposure] = Field(max_length=100_000)
    policy: EnterpriseRiskPolicy

    @model_validator(mode="after")
    def validate_ids(self) -> "EnterpriseRiskRequest":
        ids = [item.exposure_id for item in self.exposures]
        if len(ids) != len(set(ids)):
            raise ValueError("risk exposure IDs must be unique")
        return self


class EnterpriseRiskReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_loss_base: Decimal
    tail_proxy_base: Decimal
    domain_expected_losses: dict[str, Decimal]
    group_expected_losses: dict[str, Decimal]
    top_contributors: list[str]
    blockers: list[str]
    warnings: list[str]
    explanations: list[str]
    report_sha256: str
    limit_mutation_performed: bool = False
    execution_authority_granted: bool = False


def evaluate_enterprise_risk(request: EnterpriseRiskRequest) -> EnterpriseRiskReport:
    domain: dict[str, Decimal] = {}
    groups: dict[str, Decimal] = {}
    individual: list[tuple[Decimal, RiskExposure]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    unowned = 0
    for item in request.exposures:
        if item.owner.strip().lower() in {"unassigned", "unknown", "none"}:
            unowned += 1
        net = item.gross_loss_base * (Decimal("1") - item.mitigated_fraction)
        expected = net * Decimal(str(item.probability))
        domain[item.domain.value] = domain.get(item.domain.value, Decimal("0")) + expected
        groups[item.correlation_group] = groups.get(item.correlation_group, Decimal("0")) + expected
        individual.append((expected, item))
        if net > request.policy.maximum_single_exposure_base:
            blockers.append(f"single_exposure_limit:{item.exposure_id}")
    expected_total = sum(domain.values(), Decimal("0"))
    if unowned > request.policy.maximum_unowned_exposures:
        blockers.append(f"unowned_exposures:{unowned}")
    if expected_total > request.policy.maximum_expected_loss_base:
        blockers.append("expected_loss_limit")
    for name, value in domain.items():
        if expected_total > 0 and value / expected_total > request.policy.maximum_domain_fraction:
            blockers.append(f"domain_concentration:{name}")
    tail_proxy = sum(sorted(groups.values(), reverse=True)[:3], Decimal("0"))
    ranked = sorted(individual, key=lambda item: (-item[0], item[1].exposure_id))
    top = [item.exposure_id for _, item in ranked[:10]]
    explanations = []
    if ranked:
        explanations.append(f"largest_expected_loss={ranked[0][1].exposure_id}:{ranked[0][0]}")
    if domain:
        largest_domain = max(domain, key=domain.get)  # type: ignore[arg-type]
        explanations.append(f"largest_domain={largest_domain}:{domain[largest_domain]}")
    if groups:
        largest_group = max(groups, key=groups.get)  # type: ignore[arg-type]
        explanations.append(f"largest_correlation_group={largest_group}:{groups[largest_group]}")
    payload = {
        "expected_loss_base": expected_total,
        "tail_proxy_base": tail_proxy,
        "domain_expected_losses": dict(sorted(domain.items())),
        "group_expected_losses": dict(sorted(groups.items())),
        "top_contributors": top,
        "blockers": sorted(set(blockers)),
        "warnings": warnings,
        "explanations": explanations,
    }
    return EnterpriseRiskReport(**payload, report_sha256=sha256_payload(payload))
