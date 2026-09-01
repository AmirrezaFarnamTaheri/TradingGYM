"""Safe declarative strategy design, validation, and evaluation."""

from trading_gym.strategy_lab.evaluator import evaluate_strategy
from trading_gym.strategy_lab.models import StrategySpec
from trading_gym.strategy_lab.repository import StrategyRepository
from trading_gym.strategy_lab.templates import template_catalogue
from trading_gym.strategy_lab.validation import StrategyValidationReport, validate_strategy_spec

__all__ = [
    "StrategyRepository",
    "StrategySpec",
    "StrategyValidationReport",
    "evaluate_strategy",
    "template_catalogue",
    "validate_strategy_spec",
]
