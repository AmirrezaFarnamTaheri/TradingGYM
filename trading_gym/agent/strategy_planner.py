"""Slow advisory planner. Its output can only tighten the risk envelope."""

from __future__ import annotations

import json
import os
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from trading_gym.agent.intelligence.bounded_http import (
    bounded_post,
    strict_response_json,
)
from trading_gym.core.security import validate_base_url


class AgentDirective(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    regime: Literal["trend_up", "trend_down", "chop", "uncertain"]
    arm_weights: dict[str, float]
    exposure_multiplier: float = Field(ge=0, le=1)
    halted_symbols: list[str] = Field(default_factory=list, max_length=10)
    rationale: str = Field(min_length=1, max_length=1000)

    def bounded_weights(self):
        if not set(self.arm_weights).issubset({"rules", "stats", "regime"}):
            raise ValueError("planner returned an unknown arm")
        if any(
            isinstance(v, bool) or not 0 <= v <= 1 for v in self.arm_weights.values()
        ):
            raise ValueError("planner weights must remain within [0, 1]")
        return {"rules": 1.0, "stats": 1.0, "regime": 0.0, **self.arm_weights}


class LLMStrategyPlanner:
    def __init__(self, base_url, model, key_env="LLM_API_KEY", transport=None, gateway=None):
        self.base_url = validate_base_url(
            base_url, allow_loopback=True, allow_private=False, resolve_dns=False
        )
        self.model = model
        self.key_env = key_env
        self.transport = transport
        self.gateway = gateway

    def plan(self, context) -> AgentDirective:
        key = os.getenv(self.key_env, "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        instruction = (
            "You advise a spot-paper trading agent. Never submit orders. Return only JSON with regime "
            "(trend_up,trend_down,chop,uncertain), arm_weights (rules,stats in [0,1],regime 0), "
            "exposure_multiplier in [0,1], halted_symbols, and rationale. Higher risk always means smaller exposure. "
            "The account risk limits cannot be changed. A shortage of evidence means uncertain and 0 exposure."
        )
        if self.gateway:
            content = self.gateway.complete(instruction, context)
            directive = AgentDirective.model_validate_json(content)
            directive.bounded_weights()
            return directive
        with httpx.Client(
            timeout=15,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            response = bounded_post(
                client,
                self.base_url.rstrip("/") + "/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": instruction},
                        {
                            "role": "user",
                            "content": json.dumps(context, allow_nan=False),
                        },
                    ],
                    "temperature": 0,
                    "max_tokens": 700,
                },
                timeout=15,
                max_bytes=64 * 1024,
            )
        if response.status_code != 200:
            raise RuntimeError("planner provider request failed")
        data = strict_response_json(response)
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or len(content) > 16000:
            raise ValueError("invalid planner response")
        directive = AgentDirective.model_validate_json(content)
        directive.bounded_weights()
        return directive
