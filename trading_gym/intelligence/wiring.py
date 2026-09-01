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


def build_trading_policy(
    cfg: AppConfig,
    model: Any,
    env: Any,
    *,
    provider_repository: Any | None = None,
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
    )
