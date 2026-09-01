"""Canonical, venue-aware instrument and market metadata."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AssetClass(str, Enum):
    CRYPTO_SPOT = "crypto_spot"
    CRYPTO_PERPETUAL = "crypto_perpetual"
    EQUITY = "equity"
    ETF = "etf"
    FOREX = "forex"
    COMMODITY = "commodity"
    FUTURE = "future"
    INDEX = "index"


class InstrumentSpec(BaseModel):
    """Canonical instrument identity and venue trading constraints."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument_id: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9_.:-]{2,119}$")]
    venue: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9_.-]{1,39}$")]
    symbol: Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9_./:-]{0,79}$")]
    asset_class: AssetClass
    base_asset: Annotated[str | None, Field(pattern=r"^[A-Z0-9][A-Z0-9_.-]{0,39}$")] = None
    quote_asset: Annotated[str | None, Field(pattern=r"^[A-Z0-9][A-Z0-9_.-]{0,39}$")] = None
    aliases: Annotated[list[str], Field(max_length=50)] = Field(default_factory=list)
    active: bool = True
    leverage_allowed: bool = False
    short_allowed: bool = False
    max_leverage: Annotated[float, Field(ge=1.0, le=1000.0)] = 1.0
    short_borrow_cap: Annotated[float | None, Field(gt=0.0)] = None
    min_quantity: Annotated[float | None, Field(gt=0.0)] = None
    min_notional: Annotated[float | None, Field(gt=0.0)] = None
    quantity_increment: Annotated[float | None, Field(gt=0.0)] = None
    price_increment: Annotated[float | None, Field(gt=0.0)] = None
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @field_validator("instrument_id", "venue", "symbol", "base_asset", "quote_asset", mode="before")
    @classmethod
    def uppercase_identity(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("aliases", mode="before")
    @classmethod
    def normalise_aliases(cls, value: object) -> object:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("aliases must be a list")
        return [str(item).strip().upper() for item in value]

    @model_validator(mode="after")
    def validate_constraints(self) -> "InstrumentSpec":
        aliases = [item for item in self.aliases if item]
        if len(aliases) != len(set(aliases)):
            raise ValueError("aliases must be unique")
        if self.symbol in aliases or self.instrument_id in aliases:
            raise ValueError("aliases must not repeat canonical identity")
        if not self.leverage_allowed and self.max_leverage != 1.0:
            raise ValueError("max_leverage must be 1 when leverage is disabled")
        if self.short_allowed and not self.leverage_allowed:
            raise ValueError("shorting requires leverage capability")
        if self.short_borrow_cap is not None and not self.short_allowed:
            raise ValueError("short_borrow_cap requires short_allowed")
        paired = self.asset_class in {
            AssetClass.CRYPTO_SPOT,
            AssetClass.CRYPTO_PERPETUAL,
            AssetClass.FOREX,
        }
        if paired and (not self.base_asset or not self.quote_asset):
            raise ValueError("paired instruments require base_asset and quote_asset")
        return self
