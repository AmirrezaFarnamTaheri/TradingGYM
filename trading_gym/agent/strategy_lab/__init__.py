"""Safe declarative strategy design, validation, and evaluation."""

from trading_gym.agent.strategy_lab.causal_replay import (
    CausalReplayReport,
    replay_evaluated_signals,
    replay_strategy,
)
from trading_gym.agent.strategy_lab.evaluator import evaluate_strategy
from trading_gym.agent.strategy_lab.factor_catalogue import (
    FactorCandidate,
    build_factor_catalogue,
    options_at as factor_options_at,
    resolve_leaf as resolve_factor_leaf,
)
from trading_gym.agent.strategy_lab.models import StrategySpec
from trading_gym.agent.strategy_lab.repository import StrategyRepository
from trading_gym.agent.strategy_lab.templates import template_catalogue
from trading_gym.agent.strategy_lab.validation import (
    StrategyValidationReport,
    validate_strategy_spec,
)

__all__ = [
    "CausalReplayReport",
    "FactorCandidate",
    "StrategyRepository",
    "StrategySpec",
    "StrategyValidationReport",
    "build_factor_catalogue",
    "evaluate_strategy",
    "factor_options_at",
    "replay_evaluated_signals",
    "replay_strategy",
    "resolve_factor_leaf",
    "template_catalogue",
    "validate_strategy_spec",
]
