"""
Financing Rate Manager
======================
Manages daily financing rates for leveraged positions.
"""

import logging
import math
from datetime import datetime
from typing import Dict

from trading_gym.common.config import AppConfig

logger = logging.getLogger(__name__)


class FinancingRateManager:
    def __init__(self, config: AppConfig):
        financing_cfg = config.env.leverage.financing
        # Config rate is in percentage (e.g. 0.05 for 0.05%)
        # Convert to decimal: 0.05 / 100 = 0.0005
        self.daily_rate_decimal = financing_cfg.daily_pct / 100.0
        self.bar_minutes = 5
        self.rates: Dict[str, float] = {}
        self.last_updated: Dict[str, datetime] = {}
        logger.info(
            f"FinancingRateManager initialized: daily_rate={self.daily_rate_decimal:.6f} (decimal)"
        )

    def get_rate(self, symbol: str) -> float:
        """Get the daily interest rate (decimal) for a symbol."""
        return self.rates.get(symbol, self.daily_rate_decimal)

    def calculate_financing(
        self,
        symbol: str,
        borrowed_value: float,
        bars_held: int,
        bar_minutes: int = 5,
    ) -> float:
        """
        Calculate financing cost using compound interest for the period held.

        Formula:
            periods_per_day = 24 * 60 / bar_minutes
            rate_per_period = (1 + daily_rate)^(1/periods_per_day) - 1
            cost = borrowed_value * ((1 + rate_per_period)^bars_held - 1)

        Approximation for small rates/periods:
            cost ≈ borrowed_value * daily_rate * (minutes_held / 1440)

        We will use the precise compound formula.
        """
        if borrowed_value <= 0 or bars_held <= 0:
            return 0.0

        daily_rate = self.get_rate(symbol)
        minutes_per_day = 24 * 60
        periods_per_day = minutes_per_day / bar_minutes

        # Calculate rate per bar
        # (1 + r_daily) = (1 + r_bar) ^ periods_per_day
        # 1 + r_bar = (1 + r_daily) ^ (1 / periods_per_day)
        # r_bar = (1 + r_daily) ^ (1 / periods_per_day) - 1
        rate_per_bar = (1 + daily_rate) ** (1 / periods_per_day) - 1

        # Total cost is the interest accumulated over the bars held
        # If we just want the interest amount:
        # Total Amount = Principal * (1 + r_bar)^bars
        # Interest = Total Amount - Principal = Principal * ((1 + r_bar)^bars - 1)

        interest_factor = (1 + rate_per_bar) ** bars_held - 1
        cost = borrowed_value * interest_factor

        return cost

    def calculate_financing_from_minutes(
        self, symbol: str, borrowed_value: float, minutes_held: float
    ) -> float:
        """
        Calculate financing cost based on minutes held.
        """
        if borrowed_value <= 0 or minutes_held <= 0:
            return 0.0

        daily_rate = self.get_rate(symbol)
        days_held = minutes_held / 1440.0

        # Continuous compounding approximation for arbitrary time:
        # A = P * e^(rt) -> Interest = P * (e^(rt) - 1)
        # Or simply map daily rate to the fraction of day using power rule
        # Interest Factor = (1 + daily_rate)^(days_held) - 1

        interest_factor = (1 + daily_rate) ** days_held - 1
        cost = borrowed_value * interest_factor

        return cost

    def update_rate(self, symbol: str, rate: float):
        """Update the daily interest rate (decimal) for a symbol."""
        # NaN passes a `rate < 0` guard (all comparisons with NaN are False),
        # then silently poisons every financing cost derived from it; an
        # infinite rate does the same. Reject both explicitly.
        if not math.isfinite(rate) or rate < 0:
            raise ValueError("Rate must be a finite, non-negative number")
        self.rates[symbol] = rate
        # Use timezone-aware UTC
        from datetime import timezone

        self.last_updated[symbol] = datetime.now(timezone.utc)
