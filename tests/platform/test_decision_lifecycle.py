import pytest

from trading_gym.execution.governance.decision_lifecycle import DecisionRecord, transition


def test_decision_lifecycle_is_closed_revisioned_and_separation_of_duties() -> None:
    record = DecisionRecord("d1", "model.promotion", actor="proposer")
    reviewed = transition(record, "reviewed", expected_revision=0, actor="reviewer")
    assert reviewed.revision == 1
    with pytest.raises(RuntimeError, match="revision_conflict"):
        transition(reviewed, "approved", expected_revision=0, actor="approver")
    with pytest.raises(PermissionError, match="independent_approval_required"):
        transition(reviewed, "approved", expected_revision=1, actor="reviewer")
    approved = transition(reviewed, "approved", expected_revision=1, actor="approver")
    executed = transition(approved, "executed", expected_revision=2, actor="executor")
    assert executed.state == "executed"
    with pytest.raises(ValueError, match="invalid_transition"):
        transition(executed, "approved", expected_revision=3, actor="other")
