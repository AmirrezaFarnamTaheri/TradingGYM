"""Published API limits must describe actual Pydantic risk inputs."""

import json
from pathlib import Path

from trading_gym.risk.portfolio import PortfolioSnapshot, PositionExposure

ROOT = Path(__file__).resolve().parents[2]


def test_published_account_and_notional_bounds_match_runtime_schema():
    doc = json.loads((ROOT / "docs/contracts/openapi.json").read_text())
    schemas = doc["components"]["schemas"]
    for model, properties in (
        (PortfolioSnapshot, ("equity", "peak_equity", "day_start_equity")),
        (PositionExposure, ("notional",)),
    ):
        published = schemas[model.__name__]["properties"]
        runtime = model.model_json_schema()["properties"]
        for field in properties:
            assert published[field]["minimum"] == runtime[field]["minimum"]
            assert published[field]["maximum"] == runtime[field]["maximum"]
