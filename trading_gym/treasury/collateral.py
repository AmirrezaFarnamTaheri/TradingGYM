"""Deterministic collateral allocation with haircuts and settlement blockers."""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_VALUE = Decimal("1e30")


def _finite(value: Decimal, name: str, *, non_negative: bool = True) -> None:
    if not value.is_finite() or abs(value) > MAX_VALUE:
        raise ValueError(f"{name} must be finite and bounded")
    if non_negative and value < 0:
        raise ValueError(f"{name} must be non-negative")


class CollateralAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: Annotated[str, Field(min_length=1, max_length=120)]
    account_id: Annotated[str, Field(min_length=1, max_length=120)]
    market_value: Decimal
    haircut_fraction: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    encumbered_value: Decimal = Decimal("0")
    pending_settlement_value: Decimal = Decimal("0")
    liquidity_score: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    eligible: bool = True

    @model_validator(mode="after")
    def validate_values(self) -> "CollateralAsset":
        for name in ("market_value", "encumbered_value", "pending_settlement_value"):
            _finite(getattr(self, name), name)
        if self.encumbered_value + self.pending_settlement_value > self.market_value:
            raise ValueError("encumbered plus pending settlement exceeds market value")
        return self


class CapitalRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: Annotated[str, Field(min_length=1, max_length=120)]
    required_value: Decimal
    minimum_liquidity_score: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    maximum_single_asset_fraction: Decimal = Field(default=Decimal("1"), gt=0, le=1)

    @model_validator(mode="after")
    def validate_required(self) -> "CapitalRequirement":
        _finite(self.required_value, "required_value")
        if self.required_value <= 0:
            raise ValueError("required_value must be positive")
        return self


class CollateralAllocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assets: list[CollateralAsset] = Field(max_length=100_000)
    requirement: CapitalRequirement


class CollateralAllocationLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str
    account_id: str
    gross_available: Decimal
    haircut_value: Decimal
    eligible_value: Decimal
    allocated_value: Decimal


class CollateralAllocationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: str
    required_value: Decimal
    allocated_value: Decimal
    shortfall: Decimal
    coverage_ratio: Decimal
    lines: list[CollateralAllocationLine]
    blockers: list[str]
    warnings: list[str]
    mutation_performed: bool = False
    transfer_authority_granted: bool = False
    execution_authority_granted: bool = False


def allocate_collateral(request: CollateralAllocationRequest) -> CollateralAllocationReport:
    req = request.requirement
    blockers: list[str] = []
    warnings: list[str] = []
    candidates: list[tuple[CollateralAsset, Decimal, Decimal]] = []
    seen: set[str] = set()
    for asset in request.assets:
        if asset.asset_id in seen:
            blockers.append(f"duplicate_asset:{asset.asset_id}")
            continue
        seen.add(asset.asset_id)
        gross = asset.market_value - asset.encumbered_value - asset.pending_settlement_value
        eligible = gross * (Decimal("1") - asset.haircut_fraction)
        if not asset.eligible:
            warnings.append(f"ineligible_asset:{asset.asset_id}")
            continue
        if asset.liquidity_score < req.minimum_liquidity_score:
            warnings.append(f"liquidity_below_minimum:{asset.asset_id}")
            continue
        if eligible <= 0:
            continue
        candidates.append((asset, gross, eligible))
    candidates.sort(key=lambda row: (-row[0].liquidity_score, row[0].asset_id))
    remaining = req.required_value
    lines: list[CollateralAllocationLine] = []
    cap = req.required_value * req.maximum_single_asset_fraction
    for asset, gross, eligible in candidates:
        allocation = min(eligible, cap, remaining)
        if allocation <= 0:
            continue
        lines.append(
            CollateralAllocationLine(
                asset_id=asset.asset_id,
                account_id=asset.account_id,
                gross_available=gross,
                haircut_value=gross - eligible,
                eligible_value=eligible,
                allocated_value=allocation,
            )
        )
        remaining -= allocation
        if remaining <= 0:
            break
    allocated = req.required_value - max(Decimal("0"), remaining)
    shortfall = max(Decimal("0"), remaining)
    if shortfall > 0:
        blockers.append(f"collateral_shortfall:{shortfall}")
    coverage = allocated / req.required_value
    return CollateralAllocationReport(
        requirement_id=req.requirement_id,
        required_value=req.required_value,
        allocated_value=allocated,
        shortfall=shortfall,
        coverage_ratio=coverage,
        lines=lines,
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
    )
