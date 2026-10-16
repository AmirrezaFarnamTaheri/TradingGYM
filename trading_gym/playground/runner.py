"""AXIOM-1 playground runner (ADR-031): deterministic strategy simulation.

A playground run executes a declarative strategy over one (possibly
stressed) data window with explicit, bounded costs and produces an
evidence-grade result. It is the playground's imagination — and later the
self-improvement loop's evaluation engine — so it optimises for three
properties over realism:

- **Determinism.** Same inputs produce byte-identical results; a manifest
  pins sha256 hashes of window, strategy, and overlays.
- **No lookahead, machine-checked.** The decision at bar *t* uses data
  ≤ *t* (``strategy_lab.evaluate_strategy`` is rolling-window by
  construction); every fill happens at bar *t+1*'s open. The test suite
  enforces the lag.
- **Bounded costs.** Every position change pays ``fee_bps`` plus half the
  bar's ``spread_bps``; zero-fee runs show zero cost bleed.

Simulation model (long-only v1): ``entry_signal`` opens at the *next*
bar's open with ``position_fraction`` of available cash; ``exit_signal``
closes at the next bar's open; an open position at the window end closes
at the last close. The runner never touches the venue — it shares only
the strategy grammar and the frame validator with the live path.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from trading_gym.playground.scenarios import Overlay
from trading_gym.strategy_lab.evaluator import (
    evaluate_strategy,
    validate_market_frame,
)
from trading_gym.strategy_lab.models import StrategySpec

__all__ = [
    "PlaygroundRunResult",
    "PlaygroundRunSpec",
    "run_playground",
]

_DEFAULT_MAX_BARS = 200_000


def _frame_sha256(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes())
    for column in frame.columns:
        digest.update(str(column).encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True)
class PlaygroundRunSpec:
    """Everything one deterministic playground run needs."""

    window: pd.DataFrame
    strategy: StrategySpec
    overlays: tuple[Overlay, ...] = ()
    initial_capital: float = 10_000.0
    fee_bps: float = 10.0
    position_fraction: float = 1.0
    max_bars: int = _DEFAULT_MAX_BARS

    def __post_init__(self) -> None:
        if not isinstance(self.window, pd.DataFrame) or self.window.empty:
            raise ValueError("playground window must be a non-empty DataFrame")
        if len(self.window) > self.max_bars:
            raise ValueError(
                f"window has {len(self.window)} bars, above the "
                f"{self.max_bars}-bar playground limit"
            )
        validate_market_frame(self.window)
        index = self.window.index
        if (
            not isinstance(index, pd.DatetimeIndex)
            or index.tz is None
            or not index.is_unique
            or not index.is_monotonic_increasing
        ):
            raise ValueError(
                "playground bars need strictly increasing UTC-aware timestamps"
            )
        if not isinstance(self.strategy, StrategySpec):
            raise ValueError("strategy must be a StrategySpec")
        if not math.isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        # Costs >= 100% of purchase notional create zero/negative inventory.
        if not math.isfinite(self.fee_bps) or not 0 <= self.fee_bps < 10_000:
            raise ValueError("fee_bps must be finite and below 10000")
        if (
            not math.isfinite(self.position_fraction)
            or not 0.0 < self.position_fraction <= 1.0
        ):
            raise ValueError("position_fraction must be finite and within (0, 1]")
        if "spread_bps" in self.window:
            spread = self.window["spread_bps"].to_numpy(dtype=np.float64)
            if (
                not np.isfinite(spread).all()
                or (spread < 0).any()
                or self.fee_bps + spread.max() / 2 >= 10_000
            ):
                raise ValueError(
                    "spread_bps must be finite, non-negative and affordable"
                )

    def manifest(self) -> dict[str, Any]:
        return {
            "window_sha256": _frame_sha256(self.window),
            "strategy_name": self.strategy.name,
            "strategy_sha256": hashlib.sha256(
                self.strategy.model_dump_json().encode("utf-8")
            ).hexdigest(),
            "overlays": tuple(
                {"type": overlay.type, "params": dict(overlay.params)}
                for overlay in self.overlays
            ),
            "initial_capital": self.initial_capital,
            "fee_bps": self.fee_bps,
            "position_fraction": self.position_fraction,
        }


@dataclass(frozen=True)
class PlaygroundRunResult:
    """One completed run: equity, trades, metrics, and manifest."""

    equity_curve: pd.DataFrame
    trades: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]
    manifest: dict[str, Any]
    _changes: tuple[tuple[pd.Timestamp, int], ...] = ()

    def positions_change_points(
        self,
    ) -> tuple[tuple[pd.Timestamp, int], ...]:
        """Timestamped position transitions: +1 opens, -1 closes."""

        return self._changes


def run_playground(spec: PlaygroundRunSpec) -> PlaygroundRunResult:
    """Execute one deterministic simulation of ``spec``.

    The bar loop runs in the Rust kernel (ADR-035) when a compiled
    artifact is present and in the Python reference otherwise; both
    produce bit-identical results (``tests/playground/test_kernel_parity``
    enforces it), so which one ran is never observable in the output.
    """

    window = spec.window
    signals = evaluate_strategy(window, spec.strategy)
    intent = signals["position_intent"].to_numpy(dtype=np.int8)
    open_ = window["open"].to_numpy(dtype=np.float64)
    close = window["close"].to_numpy(dtype=np.float64)
    spread = (
        window["spread_bps"].to_numpy(dtype=np.float64)
        if "spread_bps" in window.columns
        else np.zeros(len(window))
    )
    index = window.index
    n = len(window)

    accelerated = _run_kernel(
        spec,
        intent=intent,
        open_=open_,
        close=close,
        spread=spread,
        index=index,
    )
    if accelerated is not None:
        equity, trades, changes, bars_in_position = accelerated
    else:
        equity, trades, changes, bars_in_position = _run_reference(
            spec,
            intent=intent,
            open_=open_,
            close=close,
            spread=spread,
            index=index,
        )

    curve = pd.DataFrame({"equity": equity}, index=index)
    returns = curve["equity"].pct_change().dropna().to_numpy(dtype=np.float64)
    running_max = np.maximum.accumulate(curve["equity"].to_numpy(dtype=np.float64))
    drawdowns = 1.0 - curve["equity"].to_numpy(dtype=np.float64) / running_max

    wins = sum(1 for trade in trades if trade["pnl_quote"] > 0)
    metrics: dict[str, Any] = {
        "n_bars": int(n),
        "initial_capital": float(spec.initial_capital),
        "final_equity": float(curve["equity"].iloc[-1]),
        "total_return_pct": float(
            (curve["equity"].iloc[-1] / spec.initial_capital - 1.0) * 100.0
        ),
        "max_drawdown": float(drawdowns.max()) if n else 0.0,
        "n_trades": len(trades),
        "win_rate": float(wins / len(trades)) if trades else 0.0,
        "exposure": float(bars_in_position / n) if n else 0.0,
        "total_costs_quote": float(sum(trade["cost_quote"] for trade in trades)),
        "returns_std": float(returns.std()) if len(returns) else 0.0,
    }

    return PlaygroundRunResult(
        equity_curve=curve,
        trades=tuple(trades),
        metrics=metrics,
        manifest=spec.manifest(),
        _changes=tuple(changes),
    )


def _run_kernel(
    spec: PlaygroundRunSpec,
    *,
    intent: np.ndarray,
    open_: np.ndarray,
    close: np.ndarray,
    spread: np.ndarray,
    index: pd.Index,
) -> (
    tuple[
        np.ndarray,
        list[dict[str, Any]],
        list[tuple[pd.Timestamp, int]],
        int,
    ]
    | None
):
    """Rust fast path; ``None`` when no kernel is loadable."""

    from trading_gym.playground import kernel as kernel_module

    result = kernel_module.simulate(
        intent=intent,
        open_=open_,
        close=close,
        spread_bps=spread,
        initial_capital=spec.initial_capital,
        fee_bps=spec.fee_bps,
        position_fraction=spec.position_fraction,
    )
    if result is None:
        return None

    trades: list[dict[str, Any]] = []
    changes: list[tuple[pd.Timestamp, int]] = []
    for trade in result.trades:
        entry_bar = trade["entry_bar"]
        exit_bar = trade["exit_bar"]
        trades.append(
            {
                "entry_ts": index[entry_bar],
                "exit_ts": index[exit_bar],
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "pnl_quote": trade["pnl_quote"],
                "cost_quote": trade["cost_quote"],
            }
        )
        changes.append((index[entry_bar], 1))
        changes.append((index[exit_bar], -1))
    changes.sort(key=lambda item: (item[0], -item[1]))
    return result.equity, trades, changes, result.bars_in_position


def _run_reference(
    spec: PlaygroundRunSpec,
    *,
    intent: np.ndarray,
    open_: np.ndarray,
    close: np.ndarray,
    spread: np.ndarray,
    index: pd.Index,
) -> tuple[
    np.ndarray,
    list[dict[str, Any]],
    list[tuple[pd.Timestamp, int]],
    int,
]:
    """Reference semantics: the Python bar loop the kernel must match."""

    cash = float(spec.initial_capital)
    units = 0.0
    entry_price = 0.0
    entry_ts: pd.Timestamp | None = None
    entry_cost = 0.0
    pending_entry = False
    pending_exit = False

    n = len(index)
    equity = np.empty(n, dtype=np.float64)
    trades: list[dict[str, Any]] = []
    changes: list[tuple[pd.Timestamp, int]] = []
    bars_in_position = 0

    def _cost_rate(bar: int) -> float:
        return (spec.fee_bps + spread[bar] / 2.0) / 10_000.0

    for bar in range(n):
        if pending_entry:
            price = open_[bar]
            notional = cash * spec.position_fraction
            cost = notional * _cost_rate(bar)
            units = (notional - cost) / price
            cash -= notional
            entry_price = price
            entry_cost = cost
            entry_ts = index[bar]
            pending_entry = False
            changes.append((index[bar], 1))
        elif pending_exit and units > 0.0:
            price = open_[bar]
            proceeds = units * price
            cost = proceeds * _cost_rate(bar)
            pnl = (units * (price - entry_price)) - cost - entry_cost
            cash += proceeds - cost
            trades.append(
                {
                    "entry_ts": entry_ts,
                    "exit_ts": index[bar],
                    "entry_price": float(entry_price),
                    "exit_price": float(price),
                    "pnl_quote": float(pnl),
                    "cost_quote": float(cost + entry_cost),
                }
            )
            units = 0.0
            pending_exit = False
            changes.append((index[bar], -1))
            entry_ts = None

        # decide from this bar's signal for the NEXT bar's open (no lookahead)
        if bar < n - 1:
            if units == 0.0 and not pending_entry and intent[bar] == 1:
                pending_entry = True
            elif units > 0.0 and not pending_exit and intent[bar] == -1:
                pending_exit = True

        equity[bar] = cash + units * close[bar]
        bars_in_position += 1 if units > 0.0 else 0

    if units > 0.0:
        # force-close an open position at the final close (mark-to-close)
        price = close[n - 1]
        proceeds = units * price
        cost = proceeds * _cost_rate(n - 1)
        pnl = (units * (price - entry_price)) - cost - entry_cost
        cash += proceeds - cost
        trades.append(
            {
                "entry_ts": entry_ts,
                "exit_ts": index[n - 1],
                "entry_price": float(entry_price),
                "exit_price": float(price),
                "pnl_quote": float(pnl),
                "cost_quote": float(cost + entry_cost),
            }
        )
        units = 0.0
        changes.append((index[n - 1], -1))
        equity[n - 1] = cash

    return equity, trades, changes, bars_in_position
