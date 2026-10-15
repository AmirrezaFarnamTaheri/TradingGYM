"""Next-open, non-authoritative strategy replay with trading costs.

This is an *indicative* research calculation, never broker execution. A
decision from completed bar t changes exposure only at bar t+1's open.
Intrabar stops, venue liquidity, partial fills and FX are not modeled.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from dataclasses import dataclass
from numbers import Real
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from trading_gym.strategy_lab.models import StrategySpec

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRICE_COLUMNS = ("open", "high", "low", "close", "volume")
_MAX_BARS = 250_000


@dataclass(frozen=True)
class CausalReplayReport:
    bars: int
    entry_count: int
    exit_count: int
    trade_count: int
    transaction_cost_base: float
    turnover_notional_base: float
    final_equity_base: float
    net_return_fraction: float
    max_drawdown_fraction: float
    position_open: bool
    ignored_final_signal: bool
    source_sha256: str
    input_sha256: str
    report_sha256: str
    execution_authority_granted: bool = False
    mutation_performed: bool = False
    venue_fills_verified: bool = False
    stop_loss_simulated: bool = False


def _bounded_real(label: str, value: object, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite numeric input")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{label} must be finite and within bounds")
    return number


def replay_evaluated_signals(
    frame: pd.DataFrame,
    *,
    source_sha256: str,
    allocation_fraction: float = 0.1,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    initial_equity_base: float = 1.0,
) -> CausalReplayReport:
    """Replay evaluated entry/exit booleans with next-open fills only.

    Caller-supplied source_sha256 is not a verified exchange attestation.
    The independent input_sha256 binds the actual OHLCV and signals.
    """
    if not isinstance(source_sha256, str) or not _SHA256.fullmatch(source_sha256):
        raise ValueError("source_sha256 must be a lowercase SHA-256 digest")
    fraction = _bounded_real(
        "allocation_fraction", allocation_fraction, low=0, high=1
    )
    if fraction == 0:
        raise ValueError("allocation_fraction must be positive")
    fee = _bounded_real("fee_bps", fee_bps, low=0, high=1000) / 10_000
    slip = _bounded_real("slippage_bps", slippage_bps, low=0, high=1000) / 10_000
    initial = _bounded_real(
        "initial_equity_base", initial_equity_base, low=0, high=1e18
    )
    if initial == 0:
        raise ValueError("initial_equity_base must be positive")
    if not isinstance(frame, pd.DataFrame) or not 2 <= len(frame) <= _MAX_BARS:
        raise ValueError("replay frame exceeds bounded sample count")
    if frame.columns.has_duplicates:
        raise ValueError("replay frame has duplicate columns")
    required = {*_PRICE_COLUMNS, "entry_signal", "exit_signal"}
    if not required.issubset(frame.columns):
        raise ValueError("replay frame is missing OHLCV or decision columns")
    if (
        frame.index.hasnans
        or not frame.index.is_unique
        or not frame.index.is_monotonic_increasing
    ):
        raise ValueError("replay timestamps must be complete and strictly increasing")
    for name in ("entry_signal", "exit_signal"):
        column = frame[name]
        if not pd.api.types.is_bool_dtype(column.dtype) or column.isna().any():
            raise ValueError("entry/exit signals must be nonnull boolean columns")
    signals = frame[["entry_signal", "exit_signal"]].to_numpy(dtype=bool)
    if np.any(signals[:, 0] & signals[:, 1]):
        raise ValueError("conflicting entry and exit signals on a bar")
    values = frame.loc[:, _PRICE_COLUMNS].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("replay requires finite OHLCV market values")
    op, hi, lo, cl, volume = (values[:, j] for j in range(5))
    if (
        np.any(op <= 0)
        or np.any(cl <= 0)
        or np.any(lo <= 0)
        or np.any(hi < np.maximum.reduce((op, cl, lo)))
        or np.any(lo > np.minimum.reduce((op, cl, hi)))
        or np.any(volume < 0)
    ):
        raise ValueError("invalid market OHLCV values")

    digest = hashlib.sha256()
    for pos, (row, signal) in enumerate(zip(values, signals)):
        stamp = str(frame.index[pos]).encode("utf-8")
        if len(stamp) > 256:
            raise ValueError("market timestamp exceeds byte budget")
        digest.update(len(stamp).to_bytes(2, "big"))
        digest.update(stamp)
        digest.update(
            struct.pack(">5d??", *row, bool(signal[0]), bool(signal[1]))
        )
    input_digest = digest.hexdigest()

    cash = initial
    units = 0.0
    entry_count = exit_count = 0
    costs = turnover = 0.0
    peak_equity = initial
    max_dd = 0.0
    for t in range(len(frame)):
        open_price = float(op[t])
        if t:
            should_enter, should_exit = signals[t - 1]
            if should_exit and units > 0:
                notional = units * open_price
                cost = notional * (fee + slip)
                cash += notional - cost
                costs += cost
                turnover += notional
                units = 0.0
                exit_count += 1
            elif should_enter and units == 0:
                notional = cash * fraction
                units = notional / (open_price * (1 + fee + slip))
                spent = units * open_price * (1 + fee + slip)
                cash -= spent
                costs += units * open_price * (fee + slip)
                turnover += units * open_price
                entry_count += 1
        equity_at_close = cash + units * float(cl[t])
        if not math.isfinite(equity_at_close) or equity_at_close < 0:
            raise ValueError("nonfinite or insolvent research portfolio")
        peak_equity = max(peak_equity, equity_at_close)
        max_dd = max(max_dd, (peak_equity - equity_at_close) / peak_equity)

    body = {
        "bars": len(frame),
        "entry_count": entry_count,
        "exit_count": exit_count,
        "trade_count": entry_count + exit_count,
        "transaction_cost_base": costs,
        "turnover_notional_base": turnover,
        "final_equity_base": equity_at_close,
        "net_return_fraction": equity_at_close / initial - 1,
        "max_drawdown_fraction": max_dd,
        "position_open": units > 0,
        "ignored_final_signal": bool(signals[-1, 0] or signals[-1, 1]),
        "source_sha256": source_sha256,
        "input_sha256": input_digest,
    }
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return CausalReplayReport(
        **body, report_sha256=hashlib.sha256(encoded).hexdigest()
    )


def replay_strategy(
    frame: pd.DataFrame,
    strategy: StrategySpec,
    *,
    source_sha256: str,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    initial_equity_base: float = 1.0,
) -> CausalReplayReport:
    """Run the canonical declarative evaluator through a read-only replay."""
    from trading_gym.strategy_lab.evaluator import evaluate_strategy

    evaluated = evaluate_strategy(frame, strategy)
    return replay_evaluated_signals(
        evaluated,
        source_sha256=source_sha256,
        allocation_fraction=strategy.risk.max_position_fraction,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        initial_equity_base=initial_equity_base,
    )
