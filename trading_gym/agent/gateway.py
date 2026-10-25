"""Real bounded model requests with persistent multi-key rotation."""

import hashlib
import json
from pathlib import Path

import httpx

from trading_gym.agent.intelligence.bounded_http import (
    bounded_post,
    strict_response_json,
)
from trading_gym.agent.intelligence.llm_key_pool import (
    CredentialPool,
    reject_terminal_provider_status,
    request_with_rotation,
)
from trading_gym.core.security import validate_base_url


class ModelGateway:
    def __init__(self, home: Path, vault, role, base_url, model):
        self.home, self.vault, self.role, self.model = Path(home), vault, role, model
        self.home.mkdir(parents=True, exist_ok=True)
        self.base_url = validate_base_url(
            base_url, allow_loopback=True, allow_private=False, resolve_dns=False
        )
        self.last_key_id = None

    def complete(self, instruction, payload, *, timeout=15):
        keys = self.vault.mapping(self.role)
        scope = hashlib.sha256(self.base_url.encode()).hexdigest()[:24]
        with httpx.Client(
            timeout=timeout, follow_redirects=False, trust_env=False
        ) as client:

            def send(key, remaining):
                self.last_key_id = next(
                    (name for name, value in keys.items() if value == key), None
                )
                return bounded_post(
                    client,
                    self.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + key} if key else {},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": instruction},
                            {
                                "role": "user",
                                "content": json.dumps(payload, allow_nan=False),
                            },
                        ],
                        "temperature": 0,
                        "max_tokens": 700,
                    },
                    timeout=remaining,
                    max_bytes=65536,
                )

            if keys:
                pool = CredentialPool(
                    tuple(keys),
                    state_path=self.home / f"rotation-{scope}.json",
                    env=keys,
                )
                response = request_with_rotation(
                    pool, send, timeout_seconds=timeout, max_attempts=len(keys)
                )
            else:
                response = send("", timeout)
                reject_terminal_provider_status(response)
            if response.status_code != 200:
                raise RuntimeError("Model gateway did not return a valid response")
            content = strict_response_json(response)["choices"][0]["message"]["content"]
            if not isinstance(content, str) or len(content) > 16000:
                raise ValueError("Invalid model response")
            return content
