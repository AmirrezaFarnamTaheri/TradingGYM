"""Policy adapter: any intelligence backend inside the SB3 trade loop.

``trade_loop`` calls ``model.predict(obs, deterministic=True)``. This adapter
exposes exactly that surface while delegating to an
:class:`~trading_gym.intelligence.base.IntelligenceBackend`, so switching the
brain requires zero loop changes. The returned action is clipped into the
environment action space; downstream clip/pre-trade-gate/coordinator still run.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.fusion_backend import FusionBackend
from trading_gym.intelligence.rl_backend import RLPolicyBackend
from trading_gym.intelligence.types import ActionKind, DecisionSignal


class IntelligencePolicyAdapter:
    """Drop-in stand-in for a BaseAlgorithm, backed by an intelligence backend."""

    def __init__(
        self,
        backend: IntelligenceBackend,
        *,
        action_low: float,
        action_high: float,
        fallback_backend: IntelligenceBackend | None = None,
        context_builder: Callable[[Any], dict[str, Any]] | None = None,
    ):
        if action_high <= action_low:
            raise ValueError("action_high must exceed action_low")
        self.backend = backend
        self.fallback = fallback_backend
        self.action_low = float(action_low)
        self.action_high = float(action_high)
        #: Populated after every predict() for evidence/telemetry surfaces.
        self.last_signal: DecisionSignal | None = None
        self._context_builder = context_builder

    def _clip(self, value: float) -> float:
        return float(min(self.action_high, max(self.action_low, value)))

    def _signal_to_action(self, signal: DecisionSignal) -> np.ndarray:
        # Action space is signed target leverage (see MarginTradingEnv).
        if signal.action_kind is ActionKind.HOLD or signal.target_leverage == 0.0:
            magnitude = 0.0
            direction = 1.0
        else:
            magnitude = abs(signal.target_leverage)
            direction = 1.0 if signal.target_leverage > 0 else -1.0
        bounded = self._clip(direction * magnitude)
        return np.array([bounded], dtype=np.float32)

    def build_context(self, observation: Any) -> dict[str, Any]:
        """Default context extraction from a MarginTradingEnv-style obs.

        The observation layout is [normalised features..., log-equity-ratio,
        log-price-ratio, normalised-position]. We expose only the tail state
        variables plus the raw feature vector length — features themselves are
        opaque to backends by design (they reason on state, not z-scores).
        """
        if self._context_builder is not None:
            return self._context_builder(observation)
        arr = np.asarray(observation, dtype=float).ravel()
        n_state = 3
        state = arr[-n_state:] if arr.size >= n_state else arr
        equity_ratio = float(np.exp(state[0])) if state.size > 0 else 1.0
        price_ratio = float(np.exp(state[1])) if state.size > 1 else 1.0
        position_norm = float(state[2]) if state.size > 2 else 0.0
        if (
            not np.isfinite(equity_ratio)
            or not np.isfinite(price_ratio)
            or equity_ratio <= 0
            or price_ratio <= 0
        ):
            raise ValueError("normalized observation ratios are invalid")
        return {
            "equity_ratio": equity_ratio,
            "price_ratio": price_ratio,
            "position_norm": position_norm,
            "n_features": int(max(0, arr.size - n_state)),
        }

    def predict(self, observation: Any, deterministic: bool = True, **_: Any):
        del deterministic  # advisory backends are always deterministic-ish
        context = self.build_context(observation)

        def to_context_obj(ctx: dict[str, Any]):
            # Bridge dict-context into the MarketContext-compatible shape the
            # backends expect, deriving sane fields where possible.
            from trading_gym.intelligence.types import MarketContext

            return MarketContext(
                symbol=str(ctx.get("symbol", "UNKNOWN")),
                position_units=float(
                    ctx.get("position_units", ctx.get("position_norm", 0.0))
                ),
                # Without environment wiring, these are dimensionless
                # observation ratios. Do not invent a $10,000 account balance.
                equity=float(ctx.get("equity", ctx.get("equity_ratio", 1.0))),
                margin_level=ctx.get("margin_level"),
                last_close=float(ctx.get("last_close", ctx.get("price_ratio", 1.0))),
                prev_close=ctx.get("prev_close"),
                recent_returns=tuple(ctx.get("recent_returns", ())),
                drawdown_from_peak=ctx.get("drawdown_from_peak"),
                extras={
                    key: float(value)
                    for key, value in ctx.items()
                    if isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and np.isfinite(value)
                },
            )

        if isinstance(self.backend, RLPolicyBackend):
            # Real RL inference consumes the environment observation directly.
            try:
                leverage, _ = self.backend.raw_predict(observation)
                kind = RLPolicyBackend.classify(leverage)
                self.last_signal = DecisionSignal(
                    backend=self.backend.name,
                    action_kind=kind,
                    target_leverage=leverage,
                    confidence=0.7,
                    rationale=f"rl policy action {leverage:+.4f}",
                )
                return self._signal_to_action(self.last_signal), None
            except Exception:
                if self.fallback is None:
                    raise
                signal = self.fallback.decide(to_context_obj(context))
                signal = type(signal)(
                    backend=f"{self.fallback.name}(degraded)",
                    action_kind=signal.action_kind,
                    target_leverage=signal.target_leverage,
                    confidence=signal.confidence,
                    rationale=f"[fallback] {signal.rationale}",
                )
                self.last_signal = signal
                return self._signal_to_action(signal), None

        try:
            if isinstance(self.backend, FusionBackend):
                signal = self.backend.decide_with_observation(
                    to_context_obj(context), observation
                )
            else:
                signal = self.backend.decide(to_context_obj(context))
        except Exception:
            if self.fallback is None:
                raise
            signal = self.fallback.decide(to_context_obj(context))
            signal = type(signal)(
                backend=f"{self.fallback.name}(degraded)",
                action_kind=signal.action_kind,
                target_leverage=signal.target_leverage,
                confidence=signal.confidence,
                rationale=f"[fallback] {signal.rationale}",
            )
        self.last_signal = signal
        return self._signal_to_action(signal), None
