"""Deterministic, non-authoritative corporate-action impact planning.

Corporate actions can invalidate quantities, cost bases, instrument identity,
and available cash without any trade taking place.  This module produces a
reviewable adjustment plan; it never mutates the accounting ledger, settles
cash, files taxes, or changes an instrument registry.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_VALUE = Decimal("1e30")


class CorporateActionType(str, Enum):
    SPLIT = "split"
    REVERSE_SPLIT = "reverse_split"
    STOCK_DIVIDEND = "stock_dividend"
    CASH_DIVIDEND = "cash_dividend"
    SYMBOL_CHANGE = "symbol_change"
    DELISTING = "delisting"


class FractionalPolicy(str, Enum):
    RETAIN = "retain"
    CASH_IN_LIEU = "cash_in_lieu"
    REJECT = "reject"


def _finite(value: Decimal | None, name: str, *, positive: bool = False, non_negative: bool = False) -> None:
    if value is None:
        return
    if not value.is_finite() or abs(value) > MAX_VALUE:
        raise ValueError(f"{name} must be finite and bounded")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if non_negative and value < 0:
        raise ValueError(f"{name} must be non-negative")


class CorporateActionHolding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: Annotated[str, Field(min_length=3, max_length=120)]
    quantity: Decimal
    average_entry_price: Decimal
    quote_currency: Annotated[str, Field(pattern=r"^[A-Z0-9]{2,20}$")]
    quantity_increment: Decimal | None = None

    @model_validator(mode="after")
    def validate_values(self) -> "CorporateActionHolding":
        _finite(self.quantity, "quantity")
        _finite(self.average_entry_price, "average_entry_price", non_negative=True)
        _finite(self.quantity_increment, "quantity_increment", positive=True)
        return self


class CorporateActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")]
    action_type: CorporateActionType
    effective_at: datetime
    holding: CorporateActionHolding
    successor_instrument_id: Annotated[str | None, Field(min_length=3, max_length=120)] = None
    ratio: Decimal | None = None
    cash_per_unit: Decimal | None = None
    cash_consideration_per_unit: Decimal | None = None
    withholding_rate: Decimal = Decimal("0")
    fractional_policy: FractionalPolicy = FractionalPolicy.RETAIN
    cash_in_lieu_price: Decimal | None = None
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def validate_contract(self) -> "CorporateActionRequest":
        if self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None:
            raise ValueError("effective_at must be timezone-aware")
        _finite(self.ratio, "ratio", positive=True)
        _finite(self.cash_per_unit, "cash_per_unit", positive=True)
        _finite(self.cash_consideration_per_unit, "cash_consideration_per_unit", non_negative=True)
        _finite(self.cash_in_lieu_price, "cash_in_lieu_price", positive=True)
        _finite(self.withholding_rate, "withholding_rate", non_negative=True)
        if self.withholding_rate > 1:
            raise ValueError("withholding_rate must not exceed 1")
        if self.action_type in {
            CorporateActionType.SPLIT,
            CorporateActionType.REVERSE_SPLIT,
            CorporateActionType.STOCK_DIVIDEND,
        } and self.ratio is None:
            raise ValueError("share-changing action requires ratio")
        if self.action_type is CorporateActionType.CASH_DIVIDEND and self.cash_per_unit is None:
            raise ValueError("cash dividend requires cash_per_unit")
        if self.action_type is CorporateActionType.SYMBOL_CHANGE and not self.successor_instrument_id:
            raise ValueError("symbol change requires successor_instrument_id")
        if self.fractional_policy is FractionalPolicy.CASH_IN_LIEU and self.cash_in_lieu_price is None:
            raise ValueError("cash-in-lieu policy requires cash_in_lieu_price")
        return self


class PositionAdjustmentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_instrument_id: str
    destination_instrument_id: str
    quantity_before: Decimal
    quantity_after: Decimal
    average_entry_price_before: Decimal
    average_entry_price_after: Decimal
    fractional_quantity: Decimal = Decimal("0")
    rationale: str


class CashAdjustmentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str
    gross_amount: Decimal
    withholding_amount: Decimal
    net_amount: Decimal
    direction: str
    rationale: str


class CorporateActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    action_type: CorporateActionType
    effective_at: datetime
    source_sha256: str
    position_adjustments: list[PositionAdjustmentProposal]
    cash_adjustments: list[CashAdjustmentProposal]
    blockers: list[str]
    warnings: list[str]
    plan_sha256: str
    ready_for_human_review: bool
    mutation_performed: bool = False
    accounting_authority_granted: bool = False
    settlement_authority_granted: bool = False
    tax_authority_granted: bool = False
    registry_authority_granted: bool = False


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def _round_to_increment(value: Decimal, increment: Decimal | None) -> tuple[Decimal, Decimal]:
    if increment is None:
        return value, Decimal("0")
    units = (value / increment).to_integral_value(rounding=ROUND_DOWN)
    rounded = units * increment
    return rounded, value - rounded


def _cash(currency: str, amount: Decimal, withholding_rate: Decimal, rationale: str) -> CashAdjustmentProposal:
    withholding = abs(amount) * withholding_rate if amount > 0 else Decimal("0")
    net = amount - withholding
    direction = "receivable" if net > 0 else "payable" if net < 0 else "none"
    return CashAdjustmentProposal(
        currency=currency,
        gross_amount=amount,
        withholding_amount=withholding,
        net_amount=net,
        direction=direction,
        rationale=rationale,
    )


def plan_corporate_action(request: CorporateActionRequest) -> CorporateActionPlan:
    holding = request.holding
    positions: list[PositionAdjustmentProposal] = []
    cash: list[CashAdjustmentProposal] = []
    blockers: list[str] = []
    warnings: list[str] = []

    destination = request.successor_instrument_id or holding.instrument_id
    if request.action_type in {CorporateActionType.SPLIT, CorporateActionType.REVERSE_SPLIT}:
        assert request.ratio is not None
        if request.action_type is CorporateActionType.SPLIT and request.ratio <= 1:
            blockers.append("split_ratio_must_exceed_one")
        if request.action_type is CorporateActionType.REVERSE_SPLIT and request.ratio >= 1:
            blockers.append("reverse_split_ratio_must_be_below_one")
        raw_quantity = holding.quantity * request.ratio
        adjusted_quantity, fractional = _round_to_increment(raw_quantity, holding.quantity_increment)
        if fractional:
            if request.fractional_policy is FractionalPolicy.RETAIN:
                adjusted_quantity, fractional = raw_quantity, Decimal("0")
            elif request.fractional_policy is FractionalPolicy.REJECT:
                blockers.append("fractional_position_requires_explicit_treatment")
            else:
                assert request.cash_in_lieu_price is not None
                cash.append(
                    _cash(
                        holding.quote_currency,
                        fractional * request.cash_in_lieu_price,
                        request.withholding_rate,
                        "fractional share cash-in-lieu proposal",
                    )
                )
        positions.append(
            PositionAdjustmentProposal(
                source_instrument_id=holding.instrument_id,
                destination_instrument_id=destination,
                quantity_before=holding.quantity,
                quantity_after=adjusted_quantity,
                average_entry_price_before=holding.average_entry_price,
                average_entry_price_after=(
                    holding.average_entry_price / request.ratio
                    if holding.quantity != 0
                    else holding.average_entry_price
                ),
                fractional_quantity=fractional,
                rationale=request.action_type.value,
            )
        )
    elif request.action_type is CorporateActionType.STOCK_DIVIDEND:
        assert request.ratio is not None
        raw_quantity = holding.quantity * (Decimal("1") + request.ratio)
        adjusted_quantity, fractional = _round_to_increment(raw_quantity, holding.quantity_increment)
        if fractional:
            if request.fractional_policy is FractionalPolicy.RETAIN:
                adjusted_quantity, fractional = raw_quantity, Decimal("0")
            elif request.fractional_policy is FractionalPolicy.REJECT:
                blockers.append("fractional_position_requires_explicit_treatment")
            else:
                assert request.cash_in_lieu_price is not None
                cash.append(_cash(
                    holding.quote_currency,
                    fractional * request.cash_in_lieu_price,
                    request.withholding_rate,
                    "stock-dividend fractional cash-in-lieu proposal",
                ))
        positions.append(PositionAdjustmentProposal(
            source_instrument_id=holding.instrument_id,
            destination_instrument_id=destination,
            quantity_before=holding.quantity,
            quantity_after=adjusted_quantity,
            average_entry_price_before=holding.average_entry_price,
            average_entry_price_after=(
                holding.average_entry_price / (Decimal("1") + request.ratio)
                if holding.quantity != 0
                else holding.average_entry_price
            ),
            fractional_quantity=fractional,
            rationale="stock_dividend",
        ))
    elif request.action_type is CorporateActionType.CASH_DIVIDEND:
        assert request.cash_per_unit is not None
        amount = holding.quantity * request.cash_per_unit
        cash.append(_cash(
            holding.quote_currency,
            amount,
            request.withholding_rate,
            "cash-dividend proposal; short positions produce a payable",
        ))
        warnings.append("dividend_entitlement_and_withholding_require_external_confirmation")
    elif request.action_type is CorporateActionType.SYMBOL_CHANGE:
        assert request.successor_instrument_id is not None
        if request.successor_instrument_id == holding.instrument_id:
            blockers.append("successor_instrument_must_differ")
        positions.append(PositionAdjustmentProposal(
            source_instrument_id=holding.instrument_id,
            destination_instrument_id=request.successor_instrument_id,
            quantity_before=holding.quantity,
            quantity_after=holding.quantity,
            average_entry_price_before=holding.average_entry_price,
            average_entry_price_after=holding.average_entry_price,
            rationale="symbol_change",
        ))
        warnings.append("instrument_registry_and_open_orders_require_separate_migration")
    elif request.action_type is CorporateActionType.DELISTING:
        if request.cash_consideration_per_unit is None:
            blockers.append("delisting_requires_confirmed_cash_consideration_or_manual_resolution")
        else:
            cash.append(_cash(
                holding.quote_currency,
                holding.quantity * request.cash_consideration_per_unit,
                request.withholding_rate,
                "delisting cash-consideration proposal",
            ))
            positions.append(PositionAdjustmentProposal(
                source_instrument_id=holding.instrument_id,
                destination_instrument_id=holding.instrument_id,
                quantity_before=holding.quantity,
                quantity_after=Decimal("0"),
                average_entry_price_before=holding.average_entry_price,
                average_entry_price_after=Decimal("0"),
                rationale="delisting",
            ))
        warnings.append("delisting_timing_claims_and_settlement_require_external_confirmation")

    payload = {
        "request": request.model_dump(mode="json"),
        "position_adjustments": [item.model_dump(mode="json") for item in positions],
        "cash_adjustments": [item.model_dump(mode="json") for item in cash],
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
    }
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    return CorporateActionPlan(
        action_id=request.action_id,
        action_type=request.action_type,
        effective_at=request.effective_at,
        source_sha256=request.source_sha256,
        position_adjustments=positions,
        cash_adjustments=cash,
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
        plan_sha256=digest,
        ready_for_human_review=not blockers,
    )
