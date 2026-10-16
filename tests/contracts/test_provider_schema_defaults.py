"""Provider OpenAPI defaults must agree with the configured Pydantic schema."""

import json
from pathlib import Path

from trading_gym.agent.providers import ProviderConfig

ROOT = Path(__file__).resolve().parents[2]


def test_nullable_provider_defaults_match_validated_runtime_model():
    document = json.loads((ROOT / "docs/contracts/openapi.json").read_text())
    published = document["components"]["schemas"]["ProviderConfig"]["properties"]
    generated = ProviderConfig.model_json_schema()["properties"]
    for field in (
        "api_key_env",
        "base_url",
        "command",
        "max_requests_per_hour",
    ):
        assert "default" in generated[field]
        assert published[field].get("default", "missing") == generated[field]["default"]
