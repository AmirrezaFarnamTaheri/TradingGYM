"""Published strategy indicator grammar must match the runtime OpenAPI model."""

import json
from pathlib import Path

from trading_gym.strategy_lab.models import IndicatorSpec

ROOT = Path(__file__).resolve().parents[2]


def test_openapi_indicator_names_include_registered_adx():
    schema = json.loads((ROOT / "docs/contracts/openapi.json").read_text())
    published = schema["components"]["schemas"]["IndicatorSpec"]["properties"]["name"]
    runtime = IndicatorSpec.model_json_schema()["properties"]["name"]
    assert "adx" in published["enum"]
    assert published["enum"] == runtime["enum"]
