"""Bounded, read-only research on user-supplied real OHLCV records."""

import hashlib
import json
from dataclasses import asdict
from typing import Literal

import numpy as np
import pandas as pd
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from trading_gym.agent.strategy_lab.adx import compute_adx
from trading_gym.agent.strategy_lab.causal_replay import replay_evaluated_signals
from trading_gym.agent.strategy_lab.indicators import rsi


class MarketBar(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    at: float = Field(gt=0)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)


class LabRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    bars: list[MarketBar] = Field(min_length=30, max_length=2000)
    strategy: Literal["ema_cross", "rsi_reversion"] = "ema_cross"
    period: int = Field(default=14, ge=2, le=100)
    fee_bps: float = Field(default=10, ge=0, le=1000)
    slippage_bps: float = Field(default=2, ge=0, le=1000)
    allocation_fraction: float = Field(default=0.1, gt=0, le=1)
    initial_capital: float = Field(default=10000, gt=0, le=1e12)


def run_lab(request: LabRequest):
    raw = [bar.model_dump() for bar in request.bars]
    source = hashlib.sha256(
        json.dumps(raw, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    frame = pd.DataFrame(raw).set_index("at")
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("timestamps must be strictly increasing")
    period = request.period
    if len(frame) < period * 2 + 1:
        raise ValueError("more bars are required for the selected indicator period")
    fast = frame.close.ewm(span=period, adjust=False, min_periods=period).mean()
    slow = frame.close.ewm(span=period * 2, adjust=False, min_periods=period * 2).mean()
    strength = rsi(frame, {"period": period})
    if request.strategy == "ema_cross":
        frame["entry_signal"] = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        frame["exit_signal"] = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    else:
        frame["entry_signal"] = (strength < 30) & (strength.shift(1) >= 30)
        frame["exit_signal"] = (strength > 70) & (strength.shift(1) <= 70)
    report = replay_evaluated_signals(
        frame,
        source_sha256=source,
        allocation_fraction=request.allocation_fraction,
        fee_bps=request.fee_bps,
        slippage_bps=request.slippage_bps,
        initial_equity_base=request.initial_capital,
    )
    adx = compute_adx(frame, period=period)

    def finite(value):
        return float(value) if np.isfinite(value) else None

    return {
        "report": asdict(report),
        "strategy": request.strategy,
        "provenance": "user_supplied_ohlcv_not_exchange_attested",
        "indicators": [
            {
                "at": float(stamp),
                "close": float(frame.loc[stamp, "close"]),
                "adx": finite(adx.loc[stamp]),
                "rsi": finite(strength.loc[stamp]),
                "fast_ema": finite(fast.loc[stamp]),
                "slow_ema": finite(slow.loc[stamp]),
            }
            for stamp in frame.index
        ],
        "limitations": [
            "Next-open indicative replay",
            "No venue fills, stops, liquidity or account mutation",
            "In-sample exploration, not holdout qualification",
        ],
    }


def mount_lab(app):
    @app.post("/api/v1/agent/strategy-lab")
    async def strategy_lab(request: LabRequest):
        import asyncio

        try:
            return await asyncio.to_thread(run_lab, request)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
