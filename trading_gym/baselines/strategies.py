from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


class Strategy(Protocol):
    """Strategy protocol used by the evaluation pipeline."""

    def reset(self) -> None:
        """Reset the strategy state (e.g., for a new episode)."""
        ...

    def act(self, t: int, prices: np.ndarray) -> float:
        """Calculate the target position/leverage given the current time step and price history."""
        ...


@dataclass
class MomentumStrategy:
    """Simple trend-following baseline: long on positive price momentum."""

    lookback: int = 1
    notional_leverage: float = 1.0

    def reset(self) -> None:
        """No state to reset for this strategy."""
        pass

    def act(self, t: int, prices: np.ndarray) -> float:
        if t < self.lookback:
            return 0.0
        direction = np.sign(prices[t] - prices[t - self.lookback])
        return float(direction * self.notional_leverage)


@dataclass
class MeanReversionStrategy:
    """Contrarian baseline that fades short-term moves."""

    lookback: int = 1
    notional_leverage: float = 1.0

    def reset(self) -> None:
        """No state to reset for this strategy."""
        pass

    def act(self, t: int, prices: np.ndarray) -> float:
        if t < self.lookback:
            return 0.0
        direction = -np.sign(prices[t] - prices[t - self.lookback])
        return float(direction * self.notional_leverage)


@dataclass
class BuyAndHoldStrategy:
    """Static buy-and-hold exposure."""

    exposure: float = 1.0

    def reset(self) -> None:
        """No state to reset for this strategy."""
        pass

    def act(self, t: int, prices: np.ndarray) -> float:
        _ = prices  # unused, but kept for API symmetry
        return float(self.exposure)


@dataclass
class RandomStrategy:
    """Randomly trades with a given probability."""

    notional_leverage: float = 1.0
    trade_prob: float = 0.1

    def reset(self) -> None:
        """No state to reset for this strategy."""
        pass

    def act(self, t: int, prices: np.ndarray) -> float:
        if np.random.rand() < self.trade_prob:
            return np.random.uniform(-self.notional_leverage, self.notional_leverage)
        return 0.0
