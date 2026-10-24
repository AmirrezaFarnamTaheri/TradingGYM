"""RL policy backend: wraps a trained SB3-style model (CPU or GPU)."""

from __future__ import annotations

from typing import Any
import math

from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.types import (
    ActionKind,
    BackendHealth,
    BackendUnavailable,
    DecisionSignal,
    InvalidDecisionSignal,
    np_finite,
    MarketContext,
)


def _cuda_available() -> bool:
    try:
        import torch  # type: ignore import-untyped

        return bool(torch.cuda.is_available())
    except Exception:  # pragma: no cover - torch optional at runtime
        return False


class RLPolicyBackend(IntelligenceBackend):
    """Advisory wrapper around a loaded policy with ``predict(obs, deterministic)``.

    The model object is injected (already deserialised by the caller through
    the checksum-verified snapshot path) so this class never touches storage.
    """

    name = "rl"

    def __init__(self, model: Any, *, device_preference: str = "auto"):
        if not hasattr(model, "predict"):
            raise ValueError("model must expose predict(obs, deterministic=...)")
        self.model = model
        self.device_preference = device_preference

    def is_available(self) -> bool:
        return self.model is not None

    def health(self) -> BackendHealth:
        cuda = _cuda_available()
        device = (
            "cuda" if cuda and self.device_preference in {"auto", "cuda"} else "cpu"
        )
        return BackendHealth(
            name=self.name,
            available=self.is_available(),
            device=device,
            detail=f"device_preference={self.device_preference}",
        )

    def decide(self, context: MarketContext) -> DecisionSignal:  # noqa: ARG002
        # The RL observation pipeline lives upstream (env features); the raw
        # signal here is derived from the same context the rule backend sees,
        # so this path stays usable for fusion arbitration without an env.
        # Real RL inference happens through `policy_adapter` against the env obs.
        raise BackendUnavailable(
            "RLPolicyBackend decides via IntelligencePolicyAdapter on env observations"
        )

    def raw_predict(self, observation: Any) -> tuple[float, None]:
        action, _ = self.model.predict(observation, deterministic=True)
        if hasattr(action, "item"):
            action = action.item()
        elif isinstance(action, (list, tuple)):
            if len(action) != 1:
                raise InvalidDecisionSignal("RL output must contain one target")
            action = action[0]
        if not np_finite(action):
            raise InvalidDecisionSignal("RL output must be one finite real target")
        return float(action), None

    def decide_with_observation(
        self, context: MarketContext, observation: Any
    ) -> DecisionSignal:
        leverage, _ = self.raw_predict(observation)
        if not math.isfinite(leverage):
            raise InvalidDecisionSignal("nonfinite RL model output")
        return DecisionSignal(
            backend=self.name,
            action_kind=self.classify(leverage),
            target_leverage=leverage,
            confidence=0.7,
            rationale="RL policy on admitted observation",
            source_id=self.name,
            confidence_semantics="advisory_score",
        )

    @staticmethod
    def classify(action_value: float) -> ActionKind:
        if action_value > 1e-9:
            return ActionKind.LONG
        if action_value < -1e-9:
            return ActionKind.SHORT
        return ActionKind.HOLD
