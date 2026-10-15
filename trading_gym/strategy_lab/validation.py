"""Static strategy validation and workload planning."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from trading_gym.market.backtest_policy import (
    BacktestLimitError,
    validate_backtest_window,
)
from trading_gym.strategy_lab.models import StrategySpec


class StrategyValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    complexity_score: int
    warmup_bars: int
    warnings: list[str]
    backtest_policy: dict[str, object] | None = None


def _indicator_warmup(name: str, params: dict[str, object]) -> int:
    if name == "macd":
        return int(params.get("slow", 26)) + int(params.get("signal", 9))
    if name == "stochastic":
        return int(params.get("period", 14)) + int(params.get("smooth", 3))
    if name == "adx":
        return 2 * int(params.get("period", 14))
    if name == "realized_volatility":
        period = int(params.get("period", 14))
        return (
            period + int(params.get("lookback", 120))
            if params.get("component", "volatility") == "percentile"
            else period + 1
        )
    if name == "mfi":
        return int(params.get("period", 14)) + 1
    return int(params.get("period", 1))


def validate_strategy_spec(
    strategy: StrategySpec,
    *,
    market: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> StrategyValidationReport:
    conditions = len(strategy.entry.conditions) + len(strategy.exit.conditions)
    complexity = len(strategy.indicators) * 3 + conditions * 2
    warmup = max(
        (
            _indicator_warmup(item.name, dict(item.params))
            for item in strategy.indicators
        ),
        default=1,
    )
    warnings: list[str] = []
    if complexity > 50:
        warnings.append("strategy complexity is high relative to the bounded grammar")
    if strategy.entry == strategy.exit:
        warnings.append("entry and exit conditions are identical")
    if strategy.risk.stop_loss_fraction is None:
        warnings.append("no explicit stop-loss is configured")
    if strategy.risk.max_leverage > 1 and strategy.risk.max_position_fraction > 0.5:
        warnings.append("large position fraction combined with leverage")
    policy = None
    if market is not None or start is not None or end is not None:
        if market is None or start is None or end is None:
            raise ValueError("market, start, and end must be supplied together")
        try:
            policy = validate_backtest_window(
                market=market,
                timeframe=strategy.timeframe,
                start=start,
                end=end,
                warmup_bars=warmup,
            )
        except BacktestLimitError as exc:
            return StrategyValidationReport(
                valid=False,
                complexity_score=complexity,
                warmup_bars=warmup,
                warnings=[*warnings, "backtest window exceeds policy"],
                backtest_policy=exc.details,
            )
    return StrategyValidationReport(
        valid=True,
        complexity_score=complexity,
        warmup_bars=warmup,
        warnings=warnings,
        backtest_policy=policy,
    )
