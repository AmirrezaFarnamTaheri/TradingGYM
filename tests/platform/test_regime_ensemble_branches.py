from axiomforge.next_level.agent_lab import regime_votes


def test_regime_votes_cover_trend_breadth_and_volatility_with_rationales() -> None:
    votes = regime_votes({"trend": 0.8, "breadth": -0.4, "volatility": 1.5})
    assert [(v.detector, v.regime) for v in votes] == [
        ("trend", "risk_on"),
        ("breadth", "risk_off"),
        ("volatility", "risk_off"),
    ]
    assert all(v.rationale and 0 <= v.confidence <= 1 for v in votes)
