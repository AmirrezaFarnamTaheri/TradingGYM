"""Intelligence configuration surface.

Optional on :class:`~trading_gym.common.config.AppConfig` — absent/None means
"legacy behaviour": the trade loop uses the loaded SB3 model exactly as before.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class IntelligenceMode(str, Enum):
    RL = "rl"
    LLM = "llm"
    SYSTEM1 = "system1"
    FUSION = "fusion"
    RULES = "rules"
    AUTO = "auto"


class IntelligenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: IntelligenceMode = IntelligenceMode.RL
    #: Provider hub id used by llm/fusion modes. Must exist in providers.json.
    llm_provider_id: Annotated[str | None, Field(max_length=80)] = None
    #: Cheap, fast advisory model fleet (priority/failover order).
    system1_provider_ids: tuple[
        Annotated[str, Field(min_length=2, max_length=80)], ...
    ] = Field(default=(), max_length=4)
    system1_confidence_floor: float = Field(default=0.65, ge=0, le=1)
    system1_max_leverage: float = Field(default=1.0, gt=0, le=5)
    system1_timeout_seconds: float = Field(default=5.0, gt=0, le=20)
    #: Only opt in when the trading lessons may be disclosed to the provider.
    system1_use_experience_memory: bool = False
    #: Settled short-term cycle P&L is sensitive; external disclosure is
    #: independently opt-in, even when experience journal access is disabled.
    system1_use_working_memory: bool = False
    #: Fusion arbitration knobs (see FusionBackend).
    fusion_leverage_cap: float = Field(default=5.0, gt=0)
    fusion_veto_confidence: float = Field(default=0.75, ge=0, le=1)
    fusion_flip_confidence: float = Field(default=0.80, ge=0, le=1)
    fusion_allow_flip: bool = True
    #: Deterministic-rule tuning for the rules floor / fallback.
    rules_momentum_window: int = Field(default=3, ge=1)
    rules_long_leverage: float = Field(default=1.0, gt=0)
    rules_short_leverage: float = Field(default=-0.5, lt=0)
    rules_defence_drawdown: float = Field(default=0.20, gt=0, lt=1)
    #: When True a backend failure falls down to the deterministic floor
    #: instead of propagating. Auto mode always degrades this way.
    allow_fallback_to_rules: bool = True
