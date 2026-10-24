from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


class Strategy(Protocol):
    """Strategy protocol used by the evaluation pipeline."""

    def reset(self, *, seed: int | None = None) -> None:
        """Reset episode state and any private randomness from an explicit seed."""
        ...

    def act(self, t: int, prices: np.ndarray) -> float:
        """Calculate the target position/leverage given the current time step and price history."""
        ...


@dataclass
class MomentumStrategy:
    """Simple trend-following baseline: long on positive price momentum."""

    lookback: int = 1
    notional_leverage: float = 1.0

    def reset(self, *, seed: int | None = None) -> None:
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

    def reset(self, *, seed: int | None = None) -> None:
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

    def reset(self, *, seed: int | None = None) -> None:
        """No state to reset for this strategy."""
        pass

    def act(self, t: int, prices: np.ndarray) -> float:
        _ = prices  # unused, but kept for API symmetry
        return float(self.exposure)


@dataclass
class RandomStrategy:
    """Random baseline with an isolated, explicitly reproducible generator."""

    notional_leverage: float = 1.0
    trade_prob: float = 0.1
    seed: int | None = None
    _rng: np.random.Generator = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not np.isfinite(self.notional_leverage) or self.notional_leverage < 0:
            raise ValueError("random strategy leverage must be finite and nonnegative")
        if not np.isfinite(self.trade_prob) or not 0 <= self.trade_prob <= 1:
            raise ValueError("random strategy trade probability must lie in [0, 1]")
        self.reset(seed=self.seed)

    def reset(self, *, seed: int | None = None) -> None:
        """Restart the generator without touching NumPy's process-global RNG."""
        self._rng = np.random.default_rng(self.seed if seed is None else seed)

    def act(self, t: int, prices: np.ndarray) -> float:
        if self._rng.random() < self.trade_prob:
            return float(
                self._rng.uniform(-self.notional_leverage, self.notional_leverage)
            )
        return 0.0
