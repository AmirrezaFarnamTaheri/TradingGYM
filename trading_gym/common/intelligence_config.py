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
    FUSION = "fusion"
    RULES = "rules"
    AUTO = "auto"


class IntelligenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: IntelligenceMode = IntelligenceMode.RL
    #: Provider hub id used by llm/fusion modes. Must exist in providers.json.
    llm_provider_id: Annotated[str | None, Field(max_length=80)] = None
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

