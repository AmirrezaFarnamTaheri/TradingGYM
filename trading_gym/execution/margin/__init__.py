"""Margin management module - Leverage and short position enforcement."""

from .borrow_caps import (  # noqa: F401  (public re-export)
    BorrowCapValidator,
)

# BorrowCapConfig is now imported from common.config, but for backward compatibility in __init__ exports
# or if other modules expect it here, we can import it from common.config
from trading_gym.core.config import BorrowCapConfig

from .financing_manager import FinancingRateManager  # noqa: F401  (public re-export)

__all__ = [
    "BorrowCapValidator",
    "BorrowCapConfig",
    "FinancingRateManager",
]
