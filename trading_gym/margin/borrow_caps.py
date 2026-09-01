from __future__ import annotations
import math
import logging
import re
from typing import Tuple
from trading_gym.common.config import BorrowCapConfig

logger = logging.getLogger(__name__)
_SYMBOL = re.compile(r"^[A-Z0-9]{2,24}$")


def _base_symbol(symbol: str, quote: str = "USDT") -> str:
    normalised = str(symbol).upper().strip().replace("/", "-")
    quote = quote.upper()
    for suffix in (f"-{quote}", quote):
        if normalised.endswith(suffix):
            normalised = normalised[: -len(suffix)]
            break
    if not _SYMBOL.fullmatch(normalised):
        raise ValueError("symbol is invalid")
    return normalised


class BorrowCapValidator:
    """
    Enforces borrowing limits (Short Selling Caps).
    """

    def __init__(self, config: BorrowCapConfig):
        self.config = config
        logger.info(
            f"BorrowCapValidator initialized. Shorting Enabled: {config.enabled}"
        )
        if config.enabled:
            logger.info(f"Allowed Short Symbols: {config.allowed_symbols}")

    def validate_short(self, symbol: str, quantity: float) -> Tuple[bool, str, str]:
        """
        Checks if a short sell order is permissible.
        Returns: (is_valid, reason_code, message)
        """
        if not self.config.enabled:
            return False, "SHORTS_DISABLED", "Short selling is globally disabled."

        try:
            base_symbol = _base_symbol(symbol)
            quantity = float(quantity)
        except (TypeError, ValueError):
            return False, "INVALID_REQUEST", "Symbol or quantity is invalid."
        if not math.isfinite(quantity) or quantity < 0:
            return False, "INVALID_QUANTITY", "Short quantity must be finite and non-negative."

        allowed = {str(item).upper() for item in self.config.allowed_symbols}

        if base_symbol not in allowed:
            return (
                False,
                "SYMBOL_NOT_ALLOWED",
                f"Shorting not allowed for {base_symbol}. Whitelist: {self.config.allowed_symbols}",
            )

        # Check Cap
        cap = float(self.config.borrow_caps.get(base_symbol, 0.0))
        if not math.isfinite(cap) or cap < 0:
            return False, "INVALID_CAP", "Configured borrow cap is invalid."
        if quantity > cap:
            return (
                False,
                "CAP_EXCEEDED",
                f"Short quantity {quantity} exceeds cap {cap} for {base_symbol}.",
            )

        return True, "OK", "Validation Passed"


# Compatibility function for BorrowCapActionClipper
def clip_short_quantity(symbol: str, quantity: float, config: BorrowCapConfig) -> float:
    """
    Clips the short quantity to the allowed maximum.
    """
    validator = BorrowCapValidator(config)
    valid, reason, _ = validator.validate_short(symbol, quantity)

    if valid:
        return quantity

    if reason == "CAP_EXCEEDED":
        base_symbol = _base_symbol(symbol)
        return config.borrow_caps.get(base_symbol, 0.0)

    return 0.0
