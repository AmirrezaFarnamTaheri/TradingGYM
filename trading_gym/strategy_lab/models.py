"""Declarative, non-executable strategy definitions."""

from __future__ import annotations

import math
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LogicMode(str, Enum):
    ALL = "all"
    ANY = "any"


class Operator(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    CROSSES_ABOVE = "crosses_above"
    CROSSES_BELOW = "crosses_below"


class IndicatorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal[
        "sma",
        "ema",
        "rsi",
        "atr",
        "macd",
        "bollinger",
        "roc",
        "vwap",
        "obv",
        "stochastic",
        "donchian",
        "adx",
    ]
    alias: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]
    params: dict[str, float | int | str | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_params(self) -> "IndicatorSpec":
        allowed = {
            "sma": {"period"},
            "ema": {"period"},
            "rsi": {"period"},
            "atr": {"period"},
            "roc": {"period"},
            "vwap": {"period"},
            "obv": set(),
            "macd": {"fast", "slow", "signal", "component"},
            "bollinger": {"period", "deviations", "component"},
            "stochastic": {"period", "smooth", "component"},
            "donchian": {"period", "component"},
            "adx": {"period", "component"},
        }[self.name]
        unknown = set(self.params) - allowed
        if unknown:
            raise ValueError(
                f"unsupported {self.name} parameters: {', '.join(sorted(unknown))}"
            )
        for key in {"period", "fast", "slow", "signal", "smooth"} & set(self.params):
            value = self.params[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be numeric")
            if (
                not math.isfinite(float(value))
                or int(value) != value
                or not 2 <= int(value) <= 10_000
            ):
                raise ValueError(f"{key} must be an integer between 2 and 10000")
        components = {
            "macd": {"line", "signal", "histogram"},
            "bollinger": {"upper", "middle", "lower"},
            "stochastic": {"k", "d"},
            "donchian": {"upper", "middle", "lower"},
            "adx": {"adx", "plus_di", "minus_di", "dx"},
        }
        if self.name in components and "component" in self.params:
            if str(self.params["component"]) not in components[self.name]:
                raise ValueError(f"invalid {self.name} component")
        if self.name == "bollinger" and "deviations" in self.params:
            deviations = float(self.params["deviations"])
            if not 0.1 <= deviations <= 10:
                raise ValueError("deviations must be between 0.1 and 10")
        if self.name == "macd":
            fast = int(self.params.get("fast", 12))
            slow = int(self.params.get("slow", 26))
            if fast >= slow:
                raise ValueError("MACD fast period must be less than slow period")
        return self


class Operand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["field", "indicator", "constant"]
    value: str | float

    @field_validator("value", mode="before")
    @classmethod
    def reject_invalid_raw_constant(cls, value: object, info: object) -> object:
        if getattr(info, "data", {}).get("kind") == "constant" and (
            isinstance(value, bool)
            or (
                isinstance(value, (int, float))
                and not math.isfinite(float(value))
            )
        ):
            raise ValueError("constant requires finite non-boolean value")
        return value

    @model_validator(mode="after")
    def validate_value(self) -> "Operand":
        if self.kind in {"field", "indicator"}:
            if not isinstance(self.value, str) or not self.value:
                raise ValueError(f"{self.kind} operand requires a non-empty name")
        elif not isinstance(self.value, (int, float)):
            raise ValueError("constant operand requires a numeric value")
        return self


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left: Operand
    operator: Operator
    right: Operand


class ConditionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: LogicMode = LogicMode.ALL
    conditions: Annotated[list[Condition], Field(min_length=1, max_length=32)]


class RiskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sizing_method: Literal[
        "fixed_fraction", "risk_per_trade", "volatility_target", "capped_kelly"
    ] = "fixed_fraction"
    max_position_fraction: Annotated[float, Field(gt=0.0, le=1.0)] = 0.1
    max_leverage: Annotated[float, Field(ge=1.0, le=1000.0)] = 1.0
    risk_per_trade_fraction: Annotated[float | None, Field(gt=0.0, le=0.2)] = None
    target_volatility: Annotated[float | None, Field(gt=0.0, le=10.0)] = None
    stop_loss_fraction: Annotated[float | None, Field(gt=0.0, lt=1.0)] = None
    take_profit_fraction: Annotated[float | None, Field(gt=0.0, le=10.0)] = None
    max_holding_bars: Annotated[int | None, Field(ge=1, le=100_000)] = None
    max_portfolio_drawdown_fraction: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.2
    max_daily_loss_fraction: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.05
    max_sector_fraction: Annotated[float, Field(gt=0.0, le=1.0)] = 0.5

    @field_validator(
        "max_position_fraction",
        "max_leverage",
        "risk_per_trade_fraction",
        "target_volatility",
        "stop_loss_fraction",
        "take_profit_fraction",
        "max_portfolio_drawdown_fraction",
        "max_daily_loss_fraction",
        "max_sector_fraction",
        "max_holding_bars",
        mode="before",
    )
    @classmethod
    def reject_boolean_risk_limit(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("numeric risk limits cannot be boolean")
        return value

    @model_validator(mode="after")
    def validate_sizing(self) -> "RiskSpec":
        if self.sizing_method == "risk_per_trade":
            if self.risk_per_trade_fraction is None or self.stop_loss_fraction is None:
                raise ValueError(
                    "risk_per_trade sizing requires risk_per_trade_fraction "
                    "and stop_loss_fraction"
                )
        if self.sizing_method == "volatility_target" and self.target_volatility is None:
            raise ValueError("volatility_target sizing requires target_volatility")
        return self


class StrategySpec(BaseModel):
    """A bounded strategy grammar; no code, imports, or callbacks are accepted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] = ""
    timeframe: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"] = "5m"
    indicators: Annotated[list[IndicatorSpec], Field(max_length=32)] = Field(
        default_factory=list
    )
    entry: ConditionGroup
    exit: ConditionGroup
    risk: RiskSpec = Field(default_factory=RiskSpec)
    tags: Annotated[list[str], Field(max_length=20)] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> "StrategySpec":
        aliases = [item.alias for item in self.indicators]
        if len(aliases) != len(set(aliases)):
            raise ValueError("indicator aliases must be unique")
        alias_set = set(aliases)
        allowed_fields = {"open", "high", "low", "close", "volume"}
        evaluator_columns = {"entry_signal", "exit_signal", "position_intent"}
        if alias_set & (allowed_fields | evaluator_columns):
            raise ValueError("indicator aliases cannot shadow reserved columns")
        for group in (self.entry, self.exit):
            for condition in group.conditions:
                for operand in (condition.left, condition.right):
                    if operand.kind == "indicator" and operand.value not in alias_set:
                        raise ValueError(f"unknown indicator alias: {operand.value}")
                    if operand.kind == "field" and operand.value not in allowed_fields:
                        raise ValueError(f"unsupported market field: {operand.value}")
        return self
