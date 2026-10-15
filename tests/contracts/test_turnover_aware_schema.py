"""The published OpenAPI must expose the complete turnover-aware contract."""

import json
from pathlib import Path

from trading_gym.institutional_os.portfolio_construction import (
    ConstructionMethod,
    PortfolioConstructionReport,
    PortfolioConstructionRequest,
)

ROOT = Path(__file__).resolve().parents[2]


def test_portfolio_optimization_method_enum_is_published():
    document = json.loads((ROOT / "docs/contracts/openapi.json").read_text())
    choices = document["components"]["schemas"]["ConstructionMethod"]["enum"]
    assert choices == [member.value for member in ConstructionMethod]


def test_new_turnover_request_and_report_fields_match_runtime_schema():
    document = json.loads((ROOT / "docs/contracts/openapi.json").read_text())
    published = document["components"]["schemas"]
    fields = (
        (PortfolioConstructionRequest, ("previous_weights", "turnover_penalty", "risk_aversion")),
        (
            PortfolioConstructionReport,
            (
                "estimated_one_way_turnover",
                "turnover_regularization",
                "objective_value",
                "previous_weights_sha256",
            ),
        ),
    )
    for model, names in fields:
        runtime = model.model_json_schema()["properties"]
        for name in names:
            assert published[model.__name__]["properties"][name] == runtime[name]
