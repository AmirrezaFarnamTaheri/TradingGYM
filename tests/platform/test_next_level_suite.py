from axiomforge.decision_lifecycle import DecisionRecord
from axiomforge.next_level.agent_lab import regime_votes


def test_next_level_primitives_are_constructible_and_explainable() -> None:
    record = DecisionRecord("d1", "regime.override")
    assert record.state == "proposed"
    votes = regime_votes({})
    assert len(votes) == 3
    assert {vote.detector for vote in votes} == {"trend", "breadth", "volatility"}
