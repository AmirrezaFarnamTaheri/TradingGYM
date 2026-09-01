"""Bounded best-execution and self-trade surveillance.

This is evidence for review, not a legal conclusion, order block, disciplinary
action, or market-abuse determination.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Side = Literal["buy", "sell"]


def _finite(value: Decimal, name: str, *, positive: bool = False) -> None:
    if not value.is_finite() or abs(value) > Decimal("1e30"):
        raise ValueError(f"{name} must be finite and bounded")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")


class ExecutionQualityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: Annotated[str, Field(min_length=1, max_length=160)]
    side: Side
    quantity: Decimal
    execution_price: Decimal
    benchmark_price: Decimal
    fee_amount: Decimal = Decimal("0")
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_values(self) -> "ExecutionQualityObservation":
        for name in ("quantity", "execution_price", "benchmark_price"):
            _finite(getattr(self, name), name, positive=True)
        _finite(self.fee_amount, "fee_amount")
        if self.fee_amount < 0:
            raise ValueError("fee_amount must be non-negative")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return self


class TradeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trade_id: Annotated[str, Field(min_length=1, max_length=160)]
    account_id: Annotated[str, Field(min_length=1, max_length=160)]
    beneficial_owner_id: Annotated[str, Field(min_length=1, max_length=160)]
    instrument_id: Annotated[str, Field(min_length=1, max_length=160)]
    side: Side
    quantity: Decimal
    price: Decimal
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_values(self) -> "TradeObservation":
        _finite(self.quantity, "quantity", positive=True)
        _finite(self.price, "price", positive=True)
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return self


class SurveillanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    executions: list[ExecutionQualityObservation] = Field(max_length=1_000_000)
    trades: list[TradeObservation] = Field(max_length=1_000_000)
    maximum_adverse_bps: float = Field(default=25.0, ge=0.0, le=100_000.0)
    self_trade_window_seconds: int = Field(default=60, ge=1, le=86_400)
    quantity_tolerance_fraction: float = Field(default=0.05, ge=0.0, le=1.0)


class SurveillanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_count: int
    trade_count: int
    weighted_adverse_bps: float
    adverse_execution_ids: list[str]
    potential_self_trade_pairs: list[str]
    blockers: list[str]
    warnings: list[str]
    legal_conclusion_reached: bool = False
    enforcement_authority_granted: bool = False
    execution_authority_granted: bool = False
    mutation_performed: bool = False


def evaluate_surveillance(request: SurveillanceRequest) -> SurveillanceReport:
    blockers: list[str] = []
    warnings: list[str] = []
    adverse: list[str] = []
    weighted_cost = Decimal("0")
    total_notional = Decimal("0")
    exec_ids: set[str] = set()
    for item in request.executions:
        if item.execution_id in exec_ids:
            blockers.append(f"duplicate_execution:{item.execution_id}")
            continue
        exec_ids.add(item.execution_id)
        direction = Decimal("1") if item.side == "buy" else Decimal("-1")
        adverse_value = direction * (item.execution_price - item.benchmark_price) * item.quantity + item.fee_amount
        notional = item.benchmark_price * item.quantity
        adverse_bps = float(adverse_value / notional * Decimal("10000"))
        if not math.isfinite(adverse_bps):
            blockers.append(f"non_finite_execution_quality:{item.execution_id}")
            continue
        weighted_cost += adverse_value
        total_notional += notional
        if adverse_bps > request.maximum_adverse_bps:
            adverse.append(item.execution_id)
    weighted_bps = float(weighted_cost / total_notional * Decimal("10000")) if total_notional else 0.0
    pairs: list[str] = []
    trades = sorted(request.trades, key=lambda x: (x.instrument_id, x.occurred_at, x.trade_id))
    trade_ids: set[str] = set()
    window = timedelta(seconds=request.self_trade_window_seconds)
    for i, left in enumerate(trades):
        if left.trade_id in trade_ids:
            blockers.append(f"duplicate_trade:{left.trade_id}")
        trade_ids.add(left.trade_id)
        for right in trades[i + 1 :]:
            if right.instrument_id != left.instrument_id:
                if right.instrument_id > left.instrument_id:
                    break
                continue
            if right.occurred_at - left.occurred_at > window:
                break
            if left.beneficial_owner_id != right.beneficial_owner_id or left.side == right.side:
                continue
            ratio = abs(left.quantity - right.quantity) / max(left.quantity, right.quantity)
            if float(ratio) <= request.quantity_tolerance_fraction:
                pairs.append(f"{left.trade_id}:{right.trade_id}")
    if adverse:
        warnings.append(f"adverse_executions:{len(adverse)}")
    if pairs:
        warnings.append(f"potential_self_trades:{len(pairs)}")
    if weighted_bps > request.maximum_adverse_bps:
        blockers.append(f"weighted_adverse_bps_exceeded:{weighted_bps:.6f}")
    return SurveillanceReport(
        execution_count=len(request.executions),
        trade_count=len(request.trades),
        weighted_adverse_bps=weighted_bps,
        adverse_execution_ids=sorted(adverse),
        potential_self_trade_pairs=sorted(set(pairs)),
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
    )
