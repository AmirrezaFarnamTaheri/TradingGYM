"""Margin management module - Leverage and short position enforcement."""

from .borrow_caps import (
    BorrowCapValidator,
)

# BorrowCapConfig lives in common.config and is re-exported here for convenience.
from trading_gym.common.config import BorrowCapConfig

from .financing_manager import FinancingRateManager

__all__ = [
    "BorrowCapValidator",
    "BorrowCapConfig",
    "FinancingRateManager",
]
