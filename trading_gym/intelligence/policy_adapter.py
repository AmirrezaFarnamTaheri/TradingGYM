"""Policy adapter: any intelligence backend inside the SB3 trade loop.

``trade_loop`` calls ``model.predict(obs, deterministic=True)``. This adapter
exposes exactly that surface while delegating to an
:class:`~trading_gym.intelligence.base.IntelligenceBackend`, so switching the
brain requires zero loop changes. The returned action is clipped into the
environment action space; downstream clip/pre-trade-gate/coordinator still run.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Sequence

import numpy as np

from trading_gym.intelligence.base import IntelligenceBackend
from trading_gym.intelligence.fusion_backend import FusionBackend
from trading_gym.intelligence.rl_backend import RLPolicyBackend
from trading_gym.intelligence.types import (
    ActionKind, BackendUnavailable, DecisionSignal, MarketContext
)


class IntelligencePolicyAdapter:
    """Drop-in stand-in for a BaseAlgorithm, backed by an intelligence backend."""

    def __init__(
        self,
        backend: IntelligenceBackend,
        *,
        action_low: float,
        action_high: float,
        fallback_backend: IntelligenceBackend | None = None,
        fallback_backends: Sequence[IntelligenceBackend] | None = None,
        context_builder: Callable[[Any], dict[str, Any]] | None = None,
    ):
        if (
            not np.isfinite(float(action_low))
            or not np.isfinite(float(action_high))
            or action_high <= action_low
        ):
            raise ValueError("action bounds must be finite and ordered")
        self.backend = backend
        self.fallback = fallback_backend
        self._fallback_chain = (
            tuple(fallback_backends)
            if fallback_backends is not None
            else ((fallback_backend,) if fallback_backend is not None else ())
        )
        ids = [id(candidate) for candidate in (backend, *self._fallback_chain)]
        if len(ids) != len(set(ids)):
            raise ValueError("intelligence fallback chain contains duplicate backends")
        self.last_failure_types: tuple[str, ...] = ()
        self.action_low = float(action_low)
        self.action_high = float(action_high)
        #: Populated after every predict() for evidence/telemetry surfaces.
        self.last_signal: DecisionSignal | None = None
        self._context_builder = context_builder

    def _clip(self, value: float) -> float:
        return float(min(self.action_high, max(self.action_low, value)))

    def _signal_to_action(self, signal: DecisionSignal) -> np.ndarray:
        # Reject contradictory decisions rather than turning a declared long
        # into a short after deriving direction from a negative numeric value.
        if (
            (signal.action_kind is ActionKind.LONG and signal.target_leverage < 0)
            or (signal.action_kind is ActionKind.SHORT and signal.target_leverage > 0)
        ):
            raise ValueError("decision direction conflicts with target leverage")
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
        arr = np.asarray(observation, dtype=float).ravel()
        # The actual observation is consumed by RL even if a custom market
        # context builder is configured. A corrupt feature cannot be ignored
        # merely because the fallback rules would not inspect that feature.
        if arr.size < 3 or not np.isfinite(arr).all():
            raise ValueError("trading observation is incomplete or nonfinite")
        if self._context_builder is not None:
            return self._context_builder(observation)
        n_state = 3
        state = arr[-n_state:]
        equity_ratio = float(np.exp(state[0]))
        price_ratio = float(np.exp(state[1]))
        position_norm = float(state[2])
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

    def _market_context(self, ctx: dict[str, Any]) -> MarketContext:
        context = MarketContext(
            symbol=str(ctx.get("symbol", "UNKNOWN")),
            position_units=float(
                ctx.get("position_units", ctx.get("position_norm", 0.0))
            ),
            # Without environment wiring, observation tails are dimensionless
            # ratios, never synthetic account balances in quote currency.
            equity=float(ctx.get("equity", ctx.get("equity_ratio", 1.0))),
            margin_level=ctx.get("margin_level"),
            last_close=float(ctx.get("last_close", ctx.get("price_ratio", 1.0))),
            prev_close=ctx.get("prev_close"),
            recent_returns=tuple(ctx.get("recent_returns", ())),
            drawdown_from_peak=ctx.get("drawdown_from_peak"),
            is_live=ctx.get("is_live", False),
            market_evidence=dict(ctx.get("market_evidence") or {}),
            risk_limits=dict(ctx.get("risk_limits") or {}),
            extras={
                key: float(value)
                for key, value in ctx.items()
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and np.isfinite(value)
            },
        )
        mandatory = (
            context.position_units,
            context.equity,
            context.last_close,
        )
        optional = (
            context.prev_close,
            context.margin_level,
            context.drawdown_from_peak,
        )
        if (
            not all(np.isfinite(value) for value in mandatory)
            or context.equity <= 0
            or context.last_close <= 0
            or any(value is not None and not np.isfinite(value) for value in optional)
            or (
                context.prev_close is not None
                and context.prev_close <= 0
            )
            or (
                context.drawdown_from_peak is not None
                and context.drawdown_from_peak < 0
            )
            or any(not np.isfinite(value) for value in context.recent_returns)
        ):
            raise ValueError("market/account context is invalid")
        return context

    @staticmethod
    def _run_backend(
        backend: IntelligenceBackend, context: MarketContext, observation: Any
    ) -> DecisionSignal:
        if isinstance(backend, FusionBackend):
            return backend.decide_with_observation(context, observation)
        if isinstance(backend, RLPolicyBackend):
            leverage, _ = backend.raw_predict(observation)
            return DecisionSignal(
                backend=backend.name,
                action_kind=RLPolicyBackend.classify(leverage),
                target_leverage=leverage,
                confidence=0.7,
                rationale=f"rl policy action {leverage:+.4f}",
            )
        return backend.decide(context)

    def predict(self, observation: Any, deterministic: bool = True, **_: Any):
        del deterministic  # downstream action gates still enforce limits
        # Invalid market/account context is a non-degradable authority failure.
        # Never manufacture fallback trades from a corrupt observation.
        context = self._market_context(self.build_context(observation))
        failures: list[str] = []
        for index, backend in enumerate((self.backend, *self._fallback_chain)):
            try:
                signal = self._run_backend(backend, context, observation)
                action = self._signal_to_action(signal)
            except PermissionError:
                # An authorization or risk policy denial is not a provider
                # outage. Switching to a lower-fidelity backend would bypass
                # the same authority that denied the original decision.
                self.last_signal = None
                self.last_failure_types = tuple((*failures, f"{backend.name}:PermissionError"))
                raise
            except Exception as exc:
                failures.append(f"{backend.name}:{type(exc).__name__}")
                continue
            if index:
                signal = replace(
                    signal,
                    backend=f"{backend.name}(degraded)",
                    rationale=f"[fallback] {signal.rationale}",
                )
            self.last_signal = signal
            self.last_failure_types = tuple(failures)
            return action, None
        self.last_signal = None
        self.last_failure_types = tuple(failures)
        raise BackendUnavailable("no configured intelligence backend produced a decision")
