"""Wiring: AppConfig + trained model + environment → intelligence policy.

The single integration seam between the omni-intelligence layer and the
existing paper/live runners. Legacy behaviour (``cfg.intelligence is None``)
returns ``None`` and callers keep using the loaded SB3 model directly.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from trading_gym.common.config import AppConfig
from trading_gym.intelligence.factory import build_intelligence_policy
from trading_gym.intelligence.policy_adapter import IntelligencePolicyAdapter

#: Intelligence modes that need a trained RL artifact at all.
MODES_REQUIRING_MODEL = frozenset({"rl", "fusion", "legacy"})


def mode_requires_model(cfg: AppConfig) -> bool:
    """True when the configured mode needs ``model_path``/``model_sha256``."""
    icfg = getattr(cfg, "intelligence", None)
    if icfg is None:
        return True
    return icfg.mode.value in MODES_REQUIRING_MODEL


def resolve_action_bounds(env: Any) -> tuple[float, float]:
    """Signed leverage bounds from the environment action space."""
    space = getattr(env, "action_space", None)
    if space is None:
        return -20.0, 20.0
    low = float(np.asarray(space.low).ravel()[0])
    high = float(np.asarray(space.high).ravel()[0])
    return low, high


def build_market_context(
    env: Any,
    *,
    symbol: str,
    risk_limits: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Extract actual account state for rules and LLM arbitration.

    Normalised observation tails are not cash balances or market prices.
    Reject missing/nonfinite source state instead of inventing dollar amounts
    or letting a fallback trade on fabricated inventory.
    """
    underlying = getattr(env, "unwrapped", env)
    prices = np.asarray(underlying.price, dtype=float)
    if prices.ndim != 1 or prices.size == 0:
        raise ValueError("environment market price history is unavailable")
    index = -1 if underlying.live_mode else int(underlying.t)
    price = float(prices[index])
    equity = float(underlying.equity)
    position = float(underlying.position)
    if (
        not all(np.isfinite(x) for x in (price, equity, position))
        or price <= 0
        or equity <= 0
    ):
        raise ValueError("environment account or price state is invalid")

    live = bool(getattr(underlying, "live_mode", False))
    verified_closes = getattr(underlying, "latest_closed_bar_closes", ())
    if live and verified_closes:
        # Mark/ticker updates are not independent completed timeframe bars.
        # Use the same closed OHLCV sequence that produced live features.
        if not isinstance(verified_closes, (list, tuple)):
            raise ValueError("invalid completed-bar history")
        closed = [float(value) for value in verified_closes[-13:]]
        if any(not np.isfinite(value) or value <= 0 for value in closed):
            raise ValueError("invalid completed-bar history")
        prev_close = closed[-1]  # latest completed close before current mark
        returns = tuple(
            (closed[i] / closed[i - 1]) - 1.0
            for i in range(len(closed) - 1, 0, -1)
        )
        return_basis = "completed_bars"
    else:
        previous = list(getattr(underlying, "price_history", ())[-9:])
        if any(
            not np.isfinite(float(value)) or float(value) <= 0
            for value in previous
        ):
            raise ValueError("environment historical prices are invalid")
        closes = [float(value) for value in previous]
        if not closes or closes[-1] != price:
            closes.append(price)
        prev_close = closes[-2] if len(closes) >= 2 else None
        returns = tuple(
            (closes[i] / closes[i - 1]) - 1.0
            for i in range(len(closes) - 1, 0, -1)
        )
        return_basis = "mark_updates" if live else "historical_bars"
    if not all(np.isfinite(value) for value in returns):
        raise ValueError("environment recent returns are invalid")

    equity_history = list(getattr(underlying, "equity_history", ())[-1000:])
    if any(not np.isfinite(float(value)) for value in equity_history):
        raise ValueError("environment equity history is invalid")
    peak = max((float(value) for value in equity_history), default=equity)
    peak = max(peak, equity)
    drawdown = max(0.0, 1.0 - equity / peak)

    margin_provider = getattr(underlying, "_margin_level", None)
    margin_level = margin_provider(price) if callable(margin_provider) else None
    if margin_level is not None:
        margin_level = float(margin_level)
        if not np.isfinite(margin_level):
            raise ValueError("environment margin state is invalid")

    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("environment symbol is unavailable")
    return {
        "symbol": symbol,
        "equity": equity,
        "position_units": position,
        "last_close": price,
        "prev_close": prev_close,
        "recent_returns": returns,
        "return_basis": return_basis,
        "drawdown_from_peak": drawdown,
        "margin_level": margin_level,
        "is_live": bool(getattr(underlying, "live_mode", False)),
        "market_evidence": dict(
            getattr(underlying, "latest_feature_provenance", {}) or {}
        ),
        "risk_limits": dict(risk_limits or {}),
        "market_features": dict(
            getattr(underlying, "latest_feature_summary", {}) or {}
        ),
    }


def build_trading_policy(
    cfg: AppConfig,
    model: Any,
    env: Any,
    *,
    provider_repository: Any | None = None,
    working_memory: Any | None = None,
) -> IntelligencePolicyAdapter | None:
    """Assemble the configured backend, or ``None`` for legacy RL-only runs.

    Configuration errors fail fast at startup (operators fix config before
    capital is at risk); runtime resilience is the degradation ladder's job.
    """
    icfg = getattr(cfg, "intelligence", None)
    if icfg is None:
        return None
    low, high = resolve_action_bounds(env)
    return build_intelligence_policy(
        config=icfg,
        model=model,
        action_low=low,
        action_high=high,
        provider_repository=provider_repository,
        working_memory=working_memory,
        context_builder=(
            (
                lambda _observation: build_market_context(
                    env,
                    symbol=cfg.env.symbol,
                    risk_limits={
                        "min_leverage": low,
                        "max_leverage": high,
                    },
                )
            )
            if getattr(cfg, "env", None) is not None
            else None
        ),
    )
