from trading_gym.execution.risk.analytics import tail_risk


def test_tail_risk_advisory_and_provenanced():
    r = tail_risk(
        [-0.02, -0.01, 0.0, 0.01, 0.02], method="cornish-fisher", provenance="fixture"
    )
    assert (
        r["advisory_only"]
        and not r["authority_granted"]
        and r["provenance"] == "fixture"
    )
