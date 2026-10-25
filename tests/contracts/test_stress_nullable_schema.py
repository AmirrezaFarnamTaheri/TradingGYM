"""Stress insolvency must be explicitly nullable in the published API."""

import json
from pathlib import Path

from trading_gym.execution.risk.stress import PortfolioStressReport

ROOT = Path(__file__).resolve().parents[2]


def test_stress_schema_uses_nullable_finite_json_risk_metrics():
    document = json.loads((ROOT / "docs/contracts/research-openapi.json").read_text())
    published = document["components"]["schemas"]["PortfolioStressReport"]
    expected = PortfolioStressReport.model_json_schema()
    for name in ("loss_fraction", "post_stress_gross_leverage"):
        assert published["properties"][name] == expected["properties"][name]
