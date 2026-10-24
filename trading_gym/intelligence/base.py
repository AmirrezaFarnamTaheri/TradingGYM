"""Backend contract shared by every intelligence source."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from trading_gym.intelligence.types import BackendHealth, DecisionSignal, MarketContext


class IntelligenceBackend(ABC):
    """A decision source.

    Implementations must be side-effect free in ``decide`` (advisory plane)
    and must raise :class:`~trading_gym.intelligence.types.BackendUnavailable`
    rather than fabricate a signal when they cannot act honestly.
    """

    name: str = "abstract"

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap liveness/capability probe (no network for local backends)."""

    @abstractmethod
    def health(self) -> BackendHealth:
        """Detailed status, including device for RL backends."""

    @abstractmethod
    def decide(self, context: MarketContext) -> DecisionSignal:
        """Produce one bounded advisory signal for the current bar."""

    def decide_with_observation(self, context: MarketContext, observation: Any) -> DecisionSignal:
        """Explicit observation seam; context-only backends ignore the tensor.

        Observation-dependent implementations override this method. Callers
        must supply the admitted, model-compatible observation; no backend
        may reconstruct a synthetic feature vector from account context.
        """
        return self.decide(context)
