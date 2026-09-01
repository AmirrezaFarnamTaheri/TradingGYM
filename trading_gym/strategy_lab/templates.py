"""Curated declarative strategy templates derived from peer strategy families."""

from __future__ import annotations

from trading_gym.strategy_lab.models import (
    Condition,
    ConditionGroup,
    IndicatorSpec,
    LogicMode,
    Operand,
    Operator,
    RiskSpec,
    StrategySpec,
)


def field(name: str) -> Operand:
    return Operand(kind="field", value=name)


def indicator(name: str) -> Operand:
    return Operand(kind="indicator", value=name)


def constant(value: float) -> Operand:
    return Operand(kind="constant", value=value)


def condition(left: Operand, operator: Operator, right: Operand) -> Condition:
    return Condition(left=left, operator=operator, right=right)


def template_catalogue() -> dict[str, StrategySpec]:
    """Return bounded examples, never recommendations or deployment authority."""

    return {
        "sma_crossover": StrategySpec(
            name="SMA crossover",
            description="Enter when fast SMA crosses above slow SMA; exit on reverse cross.",
            indicators=[
                IndicatorSpec(name="sma", alias="fast_sma", params={"period": 20}),
                IndicatorSpec(name="sma", alias="slow_sma", params={"period": 50}),
            ],
            entry=ConditionGroup(
                conditions=[
                    condition(
                        indicator("fast_sma"),
                        Operator.CROSSES_ABOVE,
                        indicator("slow_sma"),
                    )
                ]
            ),
            exit=ConditionGroup(
                conditions=[
                    condition(
                        indicator("fast_sma"),
                        Operator.CROSSES_BELOW,
                        indicator("slow_sma"),
                    )
                ]
            ),
            risk=RiskSpec(max_position_fraction=0.1, stop_loss_fraction=0.03),
            tags=["trend", "template"],
        ),
        "ema_roc_momentum": StrategySpec(
            name="EMA and ROC momentum",
            description="Require both a fast/slow EMA cross and positive rate of change.",
            indicators=[
                IndicatorSpec(name="ema", alias="ema12", params={"period": 12}),
                IndicatorSpec(name="ema", alias="ema26", params={"period": 26}),
                IndicatorSpec(name="roc", alias="roc10", params={"period": 10}),
            ],
            entry=ConditionGroup(
                mode=LogicMode.ALL,
                conditions=[
                    condition(indicator("ema12"), Operator.CROSSES_ABOVE, indicator("ema26")),
                    condition(indicator("roc10"), Operator.GT, constant(0.0)),
                ],
            ),
            exit=ConditionGroup(
                mode=LogicMode.ANY,
                conditions=[
                    condition(indicator("ema12"), Operator.CROSSES_BELOW, indicator("ema26")),
                    condition(indicator("roc10"), Operator.LT, constant(-2.0)),
                ],
            ),
            risk=RiskSpec(
                max_position_fraction=0.08,
                stop_loss_fraction=0.025,
                max_holding_bars=500,
            ),
            tags=["momentum", "confirmation", "template"],
        ),
        "rsi_mean_reversion": StrategySpec(
            name="RSI mean reversion",
            description="Enter on deeply oversold RSI and exit after normalization.",
            indicators=[IndicatorSpec(name="rsi", alias="rsi14", params={"period": 14})],
            entry=ConditionGroup(
                conditions=[condition(indicator("rsi14"), Operator.LT, constant(30))]
            ),
            exit=ConditionGroup(
                conditions=[condition(indicator("rsi14"), Operator.GT, constant(55))]
            ),
            risk=RiskSpec(max_position_fraction=0.05, stop_loss_fraction=0.025),
            tags=["mean-reversion", "template"],
        ),
        "bollinger_rsi_reversion": StrategySpec(
            name="Bollinger and RSI reversion",
            description="Require lower-band weakness and an oversold oscillator before entry.",
            indicators=[
                IndicatorSpec(
                    name="bollinger",
                    alias="bb_lower",
                    params={"period": 20, "deviations": 2.0, "component": "lower"},
                ),
                IndicatorSpec(
                    name="bollinger",
                    alias="bb_middle",
                    params={"period": 20, "deviations": 2.0, "component": "middle"},
                ),
                IndicatorSpec(name="rsi", alias="rsi14", params={"period": 14}),
            ],
            entry=ConditionGroup(
                mode=LogicMode.ALL,
                conditions=[
                    condition(field("close"), Operator.CROSSES_BELOW, indicator("bb_lower")),
                    condition(indicator("rsi14"), Operator.LT, constant(35)),
                ],
            ),
            exit=ConditionGroup(
                mode=LogicMode.ANY,
                conditions=[
                    condition(field("close"), Operator.CROSSES_ABOVE, indicator("bb_middle")),
                    condition(indicator("rsi14"), Operator.GT, constant(60)),
                ],
            ),
            risk=RiskSpec(
                sizing_method="risk_per_trade",
                max_position_fraction=0.06,
                risk_per_trade_fraction=0.01,
                stop_loss_fraction=0.02,
                max_holding_bars=200,
            ),
            tags=["mean-reversion", "volatility-band", "template"],
        ),
        "donchian_breakout": StrategySpec(
            name="Donchian breakout",
            description="Enter a prior-window high breakout and exit a shorter low-channel break.",
            indicators=[
                IndicatorSpec(
                    name="donchian",
                    alias="upper20",
                    params={"period": 20, "component": "upper"},
                ),
                IndicatorSpec(
                    name="donchian",
                    alias="lower10",
                    params={"period": 10, "component": "lower"},
                ),
            ],
            entry=ConditionGroup(
                conditions=[
                    condition(field("close"), Operator.CROSSES_ABOVE, indicator("upper20"))
                ]
            ),
            exit=ConditionGroup(
                conditions=[
                    condition(field("close"), Operator.CROSSES_BELOW, indicator("lower10"))
                ]
            ),
            risk=RiskSpec(max_position_fraction=0.08, stop_loss_fraction=0.04),
            tags=["breakout", "template"],
        ),
        "macd_trend_confirmation": StrategySpec(
            name="MACD trend confirmation",
            description="Confirm a MACD cross with price above a long EMA.",
            indicators=[
                IndicatorSpec(
                    name="macd",
                    alias="macd_line",
                    params={"fast": 12, "slow": 26, "signal": 9, "component": "line"},
                ),
                IndicatorSpec(
                    name="macd",
                    alias="macd_signal",
                    params={"fast": 12, "slow": 26, "signal": 9, "component": "signal"},
                ),
                IndicatorSpec(name="ema", alias="ema100", params={"period": 100}),
            ],
            entry=ConditionGroup(
                mode=LogicMode.ALL,
                conditions=[
                    condition(
                        indicator("macd_line"),
                        Operator.CROSSES_ABOVE,
                        indicator("macd_signal"),
                    ),
                    condition(field("close"), Operator.GT, indicator("ema100")),
                ],
            ),
            exit=ConditionGroup(
                conditions=[
                    condition(
                        indicator("macd_line"),
                        Operator.CROSSES_BELOW,
                        indicator("macd_signal"),
                    )
                ]
            ),
            risk=RiskSpec(max_position_fraction=0.07, stop_loss_fraction=0.03),
            tags=["trend", "macd", "template"],
        ),
        "vwap_rsi_reclaim": StrategySpec(
            name="VWAP reclaim with RSI",
            description="Enter a rolling VWAP reclaim only when momentum is above neutral.",
            timeframe="15m",
            indicators=[
                IndicatorSpec(name="vwap", alias="vwap20", params={"period": 20}),
                IndicatorSpec(name="rsi", alias="rsi14", params={"period": 14}),
            ],
            entry=ConditionGroup(
                mode=LogicMode.ALL,
                conditions=[
                    condition(field("close"), Operator.CROSSES_ABOVE, indicator("vwap20")),
                    condition(indicator("rsi14"), Operator.GT, constant(50)),
                ],
            ),
            exit=ConditionGroup(
                mode=LogicMode.ANY,
                conditions=[
                    condition(field("close"), Operator.CROSSES_BELOW, indicator("vwap20")),
                    condition(indicator("rsi14"), Operator.LT, constant(40)),
                ],
            ),
            risk=RiskSpec(max_position_fraction=0.05, stop_loss_fraction=0.02),
            tags=["intraday", "vwap", "template"],
        ),
        "stochastic_reversal": StrategySpec(
            name="Stochastic reversal",
            description="Use a smoothed stochastic cross from an oversold regime.",
            indicators=[
                IndicatorSpec(
                    name="stochastic",
                    alias="stoch_k",
                    params={"period": 14, "smooth": 3, "component": "k"},
                ),
                IndicatorSpec(
                    name="stochastic",
                    alias="stoch_d",
                    params={"period": 14, "smooth": 3, "component": "d"},
                ),
            ],
            entry=ConditionGroup(
                mode=LogicMode.ALL,
                conditions=[
                    condition(indicator("stoch_k"), Operator.CROSSES_ABOVE, indicator("stoch_d")),
                    condition(indicator("stoch_k"), Operator.LT, constant(30)),
                ],
            ),
            exit=ConditionGroup(
                mode=LogicMode.ANY,
                conditions=[
                    condition(indicator("stoch_k"), Operator.CROSSES_BELOW, indicator("stoch_d")),
                    condition(indicator("stoch_k"), Operator.GT, constant(80)),
                ],
            ),
            risk=RiskSpec(max_position_fraction=0.04, stop_loss_fraction=0.018),
            tags=["oscillator", "reversal", "template"],
        ),
    }
