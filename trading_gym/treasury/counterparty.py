"""Counterparty credit and net-exposure evidence without settlement authority."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_VALUE = Decimal("1e30")


def _finite(value: Decimal, name: str, *, non_negative: bool = True) -> None:
    if not value.is_finite() or abs(value) > MAX_VALUE:
        raise ValueError(f"{name} must be finite and bounded")
    if non_negative and value < 0:
        raise ValueError(f"{name} must be non-negative")


class CounterpartyExposure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    counterparty_id: Annotated[str, Field(min_length=1, max_length=160)]
    receivable: Decimal = Decimal("0")
    payable: Decimal = Decimal("0")
    posted_collateral: Decimal = Decimal("0")
    collateral_haircut_fraction: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    wrong_way_risk_multiplier: Decimal = Field(default=Decimal("1"), ge=1, le=100)
    credit_limit: Decimal
    overdue_receivable: Decimal = Decimal("0")
    disputed_amount: Decimal = Decimal("0")
    netting_set_id: Annotated[str | None, Field(min_length=1, max_length=160)] = None

    @model_validator(mode="after")
    def validate_values(self) -> "CounterpartyExposure":
        for name in ("receivable", "payable", "posted_collateral", "credit_limit", "overdue_receivable", "disputed_amount"):
            _finite(getattr(self, name), name)
        if self.credit_limit <= 0:
            raise ValueError("credit_limit must be positive")
        if self.overdue_receivable > self.receivable:
            raise ValueError("overdue_receivable exceeds receivable")
        return self


class CounterpartyRiskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exposures: list[CounterpartyExposure] = Field(max_length=100_000)
    maximum_total_exposure: Decimal
    maximum_overdue_fraction: Decimal = Field(default=Decimal("0.10"), ge=0, le=1)
    allow_cross_counterparty_netting: bool = False

    @model_validator(mode="after")
    def validate_limits(self) -> "CounterpartyRiskRequest":
        _finite(self.maximum_total_exposure, "maximum_total_exposure")
        if self.maximum_total_exposure <= 0:
            raise ValueError("maximum_total_exposure must be positive")
        return self


class CounterpartyRiskLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    counterparty_id: str
    net_unsecured_exposure: Decimal
    adjusted_exposure: Decimal
    credit_limit: Decimal
    utilisation_fraction: Decimal
    violations: list[str]


class CounterpartyRiskReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_adjusted_exposure: Decimal
    lines: list[CounterpartyRiskLine]
    violations: list[str]
    warnings: list[str]
    allowed: bool
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    mutation_performed: bool = False
    settlement_authority_granted: bool = False
    transfer_authority_granted: bool = False


def evaluate_counterparty_risk(request: CounterpartyRiskRequest) -> CounterpartyRiskReport:
    if request.allow_cross_counterparty_netting:
        raise ValueError("cross-counterparty netting is not supported")
    seen: set[str] = set()
    lines: list[CounterpartyRiskLine] = []
    violations: list[str] = []
    warnings: list[str] = []
    total = Decimal("0")
    for exposure in sorted(request.exposures, key=lambda item: item.counterparty_id):
        if exposure.counterparty_id in seen:
            violations.append(f"duplicate_counterparty:{exposure.counterparty_id}")
            continue
        seen.add(exposure.counterparty_id)
        collateral_credit = exposure.posted_collateral * (Decimal("1") - exposure.collateral_haircut_fraction)
        unsecured = max(Decimal("0"), exposure.receivable - exposure.payable - collateral_credit)
        adjusted = (unsecured + exposure.disputed_amount) * exposure.wrong_way_risk_multiplier
        utilisation = adjusted / exposure.credit_limit
        item_violations: list[str] = []
        if adjusted > exposure.credit_limit:
            item_violations.append("credit_limit_exceeded")
        overdue_fraction = exposure.overdue_receivable / exposure.receivable if exposure.receivable else Decimal("0")
        if overdue_fraction > request.maximum_overdue_fraction:
            item_violations.append("overdue_fraction_exceeded")
        if exposure.netting_set_id is None and exposure.receivable and exposure.payable:
            warnings.append(f"no_netting_agreement:{exposure.counterparty_id}")
        violations.extend(f"{exposure.counterparty_id}:{item}" for item in item_violations)
        lines.append(CounterpartyRiskLine(
            counterparty_id=exposure.counterparty_id,
            net_unsecured_exposure=unsecured,
            adjusted_exposure=adjusted,
            credit_limit=exposure.credit_limit,
            utilisation_fraction=utilisation,
            violations=item_violations,
        ))
        total += adjusted
    if not lines:
        violations.append("counterparty_exposures_empty")
    if total > request.maximum_total_exposure:
        violations.append("aggregate_counterparty_limit_exceeded")
    payload = {
        "total_adjusted_exposure": str(total),
        "lines": [line.model_dump(mode="json") for line in lines],
        "violations": sorted(set(violations)),
        "warnings": sorted(set(warnings)),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return CounterpartyRiskReport(
        total_adjusted_exposure=total,
        lines=lines,
        violations=sorted(set(violations)),
        warnings=sorted(set(warnings)),
        allowed=not violations,
        report_sha256=digest,
    )
